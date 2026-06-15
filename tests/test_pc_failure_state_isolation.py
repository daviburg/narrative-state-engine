"""PC failure-tracking state isolation and cooldown tests (#508).

These tests prove the #508 fix: the per-run ``_PCFailureState`` object preserves
the cross-turn PC skip/cooldown semantics byte-identically at the production
default (sequential, ``parallel_workers: 1``) while isolating concurrent
``extract_and_merge()`` runs so one run cannot corrupt another's counters.
"""
import os
import sys
import threading
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMExtractionError
import semantic_extraction as se


def _fresh_catalogs():
    catalogs = {fn: [] for fn in se.CATALOG_KEYS}
    se._ensure_player_character(catalogs, "turn-001")
    return catalogs


def _make_failing_llm():
    """Stub LLM whose PC detail extraction always fails."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        sp = system_prompt.lower()
        if "discover" in sp or "discovery" in sp:
            return {"entities": []}
        if "detail" in sp:
            raise LLMExtractionError("Simulated PC extraction failure")
        if "relationship" in sp:
            return {"relationships": []}
        if "event" in sp:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _make_success_llm():
    """Stub LLM whose PC detail extraction always succeeds."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        sp = system_prompt.lower()
        if "discover" in sp or "discovery" in sp:
            return {"entities": []}
        if "detail" in sp:
            return {"entity": {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in sp:
            return {"relationships": []}
        if "event" in sp:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _detail_call_count(llm):
    """How many PC-detail extraction calls the stub has seen."""
    n = 0
    for call in llm.extract_json.call_args_list:
        kwargs = call[1] or {}
        capture = kwargs.get("capture") or {}
        if (capture.get("phase") == "entity_detail"
                and capture.get("entity_id") == "char-player"):
            n += 1
    return n


def _reference_decision_sequence(num_turns):
    """Replicate the pre-#508 cooldown algorithm to compute the per-turn
    attempt/skip decisions, using the frozen constants only."""
    cf = 0
    tsc = 0
    decisions = []
    for _ in range(num_turns):
        skip = se._should_skip_pc(cf, tsc)
        decisions.append("skip" if skip else "attempt")
        if cf >= se._PC_SKIP_THRESHOLD:
            tsc += 1
        if not skip:
            # Stub fails every attempt → consecutive failures increment.
            cf += 1
    return decisions


class TestSequentialCooldownByteIdentical:
    """The per-run state object preserves the exact sequential decisions."""

    def _drive(self, monkeypatch, num_turns):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_failing_llm()
        catalogs = _fresh_catalogs()
        events = []
        observed = []
        prev_detail = 0
        for i in range(num_turns):
            turn = {
                "turn_id": f"turn-{i + 1:03d}",
                "speaker": "dm",
                "text": f"The DM describes turn {i + 1}.",
            }
            catalogs, events, _failed, _log = se.extract_and_merge(
                turn, catalogs, events, llm, min_confidence=0.6,
            )
            now = _detail_call_count(llm)
            observed.append("attempt" if now > prev_detail else "skip")
            prev_detail = now
        return observed

    def test_skip_trips_exactly_at_threshold(self, monkeypatch):
        """PC extraction is attempted up to the threshold, then skipped."""
        # threshold attempts that fail, then the next turn is skipped.
        observed = self._drive(monkeypatch, se._PC_SKIP_THRESHOLD + 1)
        assert observed[: se._PC_SKIP_THRESHOLD] == ["attempt"] * se._PC_SKIP_THRESHOLD
        assert observed[se._PC_SKIP_THRESHOLD] == "skip"
        state = se._get_pc_state()
        assert state.consecutive_failures == se._PC_SKIP_THRESHOLD

    def test_full_cooldown_cycle_matches_reference(self, monkeypatch):
        """Drive through a full cooldown + retry window and compare the entire
        attempt/skip sequence against the frozen-constant reference."""
        num_turns = (
            se._PC_SKIP_THRESHOLD
            + se._PC_SKIP_COOLDOWN
            + se._PC_RETRY_WINDOW
            + 5
        )
        observed = self._drive(monkeypatch, num_turns)
        expected = _reference_decision_sequence(num_turns)
        assert observed == expected
        # Cooldown skips exactly match the frozen-constant reference: the first
        # _PC_SKIP_COOLDOWN turns after the threshold are skipped, attempts
        # resume for _PC_RETRY_WINDOW turns, then cooldown begins again.
        state = se._get_pc_state()
        assert state.skipped_turns == expected.count("skip")
        assert expected[
            se._PC_SKIP_THRESHOLD : se._PC_SKIP_THRESHOLD + se._PC_SKIP_COOLDOWN
        ] == ["skip"] * se._PC_SKIP_COOLDOWN
        assert expected[
            se._PC_SKIP_THRESHOLD + se._PC_SKIP_COOLDOWN :
            se._PC_SKIP_THRESHOLD + se._PC_SKIP_COOLDOWN + se._PC_RETRY_WINDOW
        ] == ["attempt"] * se._PC_RETRY_WINDOW

    def test_success_resets_counter_across_turns(self, monkeypatch):
        """A success clears the consecutive-failure counter and the cooldown
        position, and the next turn attempts PC extraction again."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        fail_llm = _make_failing_llm()
        catalogs = _fresh_catalogs()
        events = []
        for i in range(5):
            turn = {"turn_id": f"turn-{i + 1:03d}", "speaker": "dm", "text": "t"}
            catalogs, events, _f, _l = se.extract_and_merge(
                turn, catalogs, events, fail_llm, min_confidence=0.6,
            )
        state = se._get_pc_state()
        assert state.consecutive_failures == 5

        ok_llm = _make_success_llm()
        turn = {"turn_id": "turn-006", "speaker": "dm", "text": "t"}
        se.extract_and_merge(turn, catalogs, events, ok_llm, min_confidence=0.6)
        assert state.consecutive_failures == 0
        assert state.turns_since_cooldown == 0


class TestConcurrentRunIsolation:
    """Two simultaneous runs must not corrupt each other's PC counters."""

    def test_failing_run_does_not_push_clean_run_into_cooldown(self, monkeypatch):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")

        num_turns = se._PC_SKIP_THRESHOLD + 15  # enough to trip + skip
        barrier = threading.Barrier(2)
        results = {}

        def _run(name, llm_factory):
            # Each worker installs its OWN per-run PC state (its own thread
            # context) exactly like a retry_failed_turns worker (#508).
            se._reset_pc_failure_tracking()
            state = se._get_pc_state()
            llm = llm_factory()
            catalogs = _fresh_catalogs()
            events = []
            for i in range(num_turns):
                # Lockstep so a shared-global bug would interleave and corrupt.
                barrier.wait()
                turn = {
                    "turn_id": f"{name}-turn-{i + 1:03d}",
                    "speaker": "dm",
                    "text": f"{name} turn {i + 1}",
                }
                catalogs, events, _f, _l = se.extract_and_merge(
                    turn, catalogs, events, llm, min_confidence=0.6,
                )
            results[name] = state

        t_fail = threading.Thread(target=_run, args=("fail", _make_failing_llm))
        t_ok = threading.Thread(target=_run, args=("ok", _make_success_llm))
        t_fail.start()
        t_ok.start()
        t_fail.join()
        t_ok.join()

        # The all-failing run trips the threshold and enters cooldown.
        assert results["fail"].consecutive_failures == se._PC_SKIP_THRESHOLD
        assert results["fail"].skipped_turns == num_turns - se._PC_SKIP_THRESHOLD

        # The all-succeeding run is completely unaffected by the other run's
        # failures: it never accumulates failures and never skips.
        assert results["ok"].consecutive_failures == 0
        assert results["ok"].skipped_turns == 0
        assert results["ok"].turns_since_cooldown == 0

        # The two runs held DISTINCT state objects (isolation).
        assert results["fail"] is not results["ok"]

    def test_two_failing_runs_keep_independent_counters(self, monkeypatch):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")

        turns_a = se._PC_SKIP_THRESHOLD + 10
        turns_b = 5
        results = {}

        def _run(name, num_turns):
            se._reset_pc_failure_tracking()
            state = se._get_pc_state()
            llm = _make_failing_llm()
            catalogs = _fresh_catalogs()
            events = []
            for i in range(num_turns):
                turn = {
                    "turn_id": f"{name}-turn-{i + 1:03d}",
                    "speaker": "dm",
                    "text": f"{name} turn {i + 1}",
                }
                catalogs, events, _f, _l = se.extract_and_merge(
                    turn, catalogs, events, llm, min_confidence=0.6,
                )
            results[name] = state

        t_a = threading.Thread(target=_run, args=("a", turns_a))
        t_b = threading.Thread(target=_run, args=("b", turns_b))
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()

        # Run A tripped the threshold; run B stayed well below it. If the
        # counters leaked, B would show A's inflated failure count.
        assert results["a"].consecutive_failures == se._PC_SKIP_THRESHOLD
        assert results["a"].skipped_turns == turns_a - se._PC_SKIP_THRESHOLD
        assert results["b"].consecutive_failures == turns_b
        assert results["b"].skipped_turns == 0


class TestRetryFailedTurnsWorkerLifetime:
    """Exercise the production ``retry_failed_turns`` path (#508).

    These regressions fail if the per-run PC state is reset per *task* (per
    retried turn) instead of once per *worker thread*.  They drive the real
    ``extract_single_turn`` through a ``ThreadPoolExecutor`` configured exactly
    like production — ``initializer=_init_pc_worker`` — so the PC state lifetime
    matches what ``run_retry`` builds.
    """

    @staticmethod
    def _import_rft(monkeypatch):
        import retry_failed_turns as rft

        # Avoid constructing a real LLM client / doing real extraction.
        monkeypatch.setattr(rft, "LLMClient", lambda *a, **k: MagicMock())
        return rft

    @staticmethod
    def _make_counting_extract(observations, lock, barrier=None):
        """Stub ``extract_and_merge`` that mutates the current worker's PC state
        the way a failing turn would and records which state object it saw."""
        def _stub(turn, catalogs, events, llm, min_confidence,
                  catalog_dir=None, timeline=None):
            # When a barrier is supplied, block until every worker is active so
            # each task is guaranteed to run on a distinct worker thread.
            if barrier is not None:
                barrier.wait()
            state = se._get_pc_state()
            state.consecutive_failures += 1
            with lock:
                observations.append(
                    (turn["turn_id"], id(state), state.consecutive_failures)
                )
            log_record = {"turn_id": turn["turn_id"], "elapsed_ms": 0}
            return catalogs, events, True, log_record
        return _stub

    def _run_through_pool(self, rft, turns, num_workers, use_barrier=False):
        observations = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_workers) if use_barrier else None
        rft.extract_and_merge = self._make_counting_extract(
            observations, lock, barrier
        )
        catalogs = _fresh_catalogs()
        events = []
        timeline = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(
            max_workers=num_workers, initializer=rft._init_pc_worker
        ) as pool:
            futures = [
                pool.submit(
                    rft.extract_single_turn,
                    turn, catalogs, events, timeline,
                    "config.json", None, 0.6, "catalog_dir",
                )
                for turn in turns
            ]
            for f in as_completed(futures):
                f.result()
        return observations

    def test_sequential_worker_accumulates_failures_across_turns(self, monkeypatch):
        """With ``--workers 1`` the sequential retried turns share ONE state
        object whose ``consecutive_failures`` accumulates 1, 2, 3, ... — the
        pre-#508 cross-turn cooldown semantics.  A per-task reset would make
        every turn report a fresh object with count 1."""
        rft = self._import_rft(monkeypatch)
        turns = [
            {"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"t{i}"}
            for i in range(1, 4)
        ]
        observations = self._run_through_pool(rft, turns, num_workers=1)

        # Sequential execution on a single worker preserves submission order.
        counts = [c for (_tid, _sid, c) in observations]
        assert counts == [1, 2, 3], (
            "consecutive_failures must accumulate across sequential retry turns; "
            f"got {counts} (a per-turn reset would yield [1, 1, 1])"
        )
        # All three turns saw the SAME state object.
        state_ids = {sid for (_tid, sid, _c) in observations}
        assert len(state_ids) == 1

    def test_concurrent_workers_do_not_share_state_object(self, monkeypatch):
        """Production workers must each get their OWN ``_PCFailureState`` so a
        failing worker cannot corrupt a concurrent worker's counters."""
        rft = self._import_rft(monkeypatch)
        num_workers = 3
        turns = [
            {"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"t{i}"}
            for i in range(1, num_workers + 1)
        ]
        observations = self._run_through_pool(
            rft, turns, num_workers=num_workers, use_barrier=True
        )

        # Each turn ran on its own worker thread, so each must have observed a
        # distinct state object that had not already accumulated failures.
        state_ids = {sid for (_tid, sid, _c) in observations}
        assert len(state_ids) == num_workers
        counts = sorted(c for (_tid, _sid, c) in observations)
        assert counts == [1, 1, 1]
