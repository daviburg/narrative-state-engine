"""Behavioral tests for personal-name coreference handling (#524).

These tests exercise the *deterministic* discovery-filter pipeline that the
coreference fix touches â€” `_build_compound_word_index()` +
`_is_compound_term_fragment()` â€” using the same two-step filter the production
discovery phase applies at its call site.  The coref *decision* (continuity
callback vs. fresh introduction) is made by the LLM via the discovery template;
these tests verify that, given the discovery records the model is instructed to
emit, the pipeline keeps the right ones and drops the right ones.

Scenarios:
  1. "Mara Veylin" then later bare "Mara" continuity callback -> kept (resolves
     to the existing id; not dropped as a compound fragment).
  2. "Mara Veylin" + later "Joren Veylin" (conflicting given name) -> kept as a
     NEW multi-word entity (a distinct id is minted).
  3. "Mara Veylin" + later a fresh-introduction "Mara, the baker" minted as a
     NEW person -> a distinctly-named new person is kept; a bare single-word
     new proposal that collides with the compound is dropped by the #398 guard
     (the documented precision/recall tradeoff).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _GENERIC_STEMS,
    _build_compound_word_index,
    _build_known_id_set,
    _expand_compact_discovery_entries,
    _is_compound_term_fragment,
    _partition_detail_tasks,
    _run_discovery_phase,
    _strip_any_prefix,
    _validate_existing_ids,
    find_entity_by_id,
    get_entity_id,
)
import semantic_extraction as se
from unittest.mock import MagicMock
from catalog_merger import CATALOG_KEYS


def _apply_discovery_filter(catalogs: dict, qualified: list[dict]) -> list[dict]:
    """Mirror the production compound-fragment filter loop (#398 call site).

    Builds the compound-word index from the catalog plus the current-turn
    candidates and returns the entities that survive the compound-fragment
    rejection â€” exactly as `_run_discovery_phase` does before within-turn dedup.
    A reference is spared only when its existing_id resolves against the real
    catalog (#524), so the helper passes the known-id set through.
    """
    index = _build_compound_word_index(catalogs, qualified)
    known_ids = _build_known_id_set(catalogs)
    kept: list[dict] = []
    for entity in qualified:
        is_frag, _ = _is_compound_term_fragment(entity, index, known_ids)
        if is_frag:
            continue
        kept.append(entity)
    return kept


def _catalog_with(*characters: dict) -> dict:
    return {"characters.json": list(characters)}


# ---------------------------------------------------------------------------
# Scenario 1 â€” continuity callback resolves to the existing id
# ---------------------------------------------------------------------------

class TestContinuityCallbackKept:
    def test_bare_given_name_callback_resolves_to_existing_id(self):
        """A bare "Mara" callback carrying existing_id survives the filter."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        callback = {
            "name": "Mara",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
            "confidence": 0.95,
        }
        kept = _apply_discovery_filter(catalogs, [callback])
        assert kept == [callback]
        # The reference still points at the existing id (no fragmentation).
        assert kept[0]["existing_id"] == "char-mara-veylin"

    def test_compact_known_entity_form_callback_kept(self):
        """The compact `{existing_id, confidence}` callback form is preserved."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        compact = {"existing_id": "char-mara-veylin", "confidence": 0.9}
        kept = _apply_discovery_filter(catalogs, [compact])
        assert kept == [compact]

    def test_bare_surname_callback_resolves_to_existing_id(self):
        """A bare "Veylin" callback carrying existing_id survives the filter."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        callback = {
            "name": "Veylin",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
        }
        kept = _apply_discovery_filter(catalogs, [callback])
        assert kept == [callback]


# ---------------------------------------------------------------------------
# Scenario 2 â€” conflicting second component mints a NEW id
# ---------------------------------------------------------------------------

class TestConflictingComponentMintsNewId:
    def test_joren_veylin_is_kept_as_new_distinct_entity(self):
        """A NEW "Joren Veylin" (shared surname, conflicting given name) is kept."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        joren = {
            "name": "Joren Veylin",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-joren-veylin",
            "confidence": 0.9,
        }
        kept = _apply_discovery_filter(catalogs, [joren])
        # Multi-word personal name is never a compound-term fragment; it survives
        # so a distinct id can be minted rather than fused into Mara Veylin.
        assert kept == [joren]
        assert kept[0]["proposed_id"] == "char-joren-veylin"


# ---------------------------------------------------------------------------
# Scenario 3 â€” fresh introduction of a new person
# ---------------------------------------------------------------------------

class TestFreshIntroductionMintsNewId:
    def test_distinctly_named_new_person_is_kept(self):
        """A fresh-introduction new person with a distinct full name is kept."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        new_person = {
            "name": "Mara Stonefield",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-mara-stonefield",
            "confidence": 0.85,
        }
        kept = _apply_discovery_filter(catalogs, [new_person])
        assert kept == [new_person]
        assert kept[0]["proposed_id"] == "char-mara-stonefield"

    def test_bare_single_word_new_proposal_dropped_by_398_guard(self):
        """A bare single-word NEW proposal colliding with a compound is dropped.

        This documents the precision/recall tradeoff (LOW caveat): the #398
        compound-fragment guard still drops a single-word NEW name that exactly
        matches a component of an existing compound, because such a proposal is
        indistinguishable from a fragment.  The guard ONLY spares known
        references (existing_id / is_new=False) â€” so a fresh-introduction new
        person should be emitted with a distinguishing form (see the kept case
        above), not a bare colliding token.
        """
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        bare_new = {
            "name": "Mara",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-mara-baker",
        }
        kept = _apply_discovery_filter(catalogs, [bare_new])
        # Dropped: the guard must not over-spare unqualified new proposals, or
        # #398 fragment rejection would be defeated entirely.
        assert kept == []


# ---------------------------------------------------------------------------
# Mixed-turn end-to-end: callback kept, new distinct person kept together
# ---------------------------------------------------------------------------

class TestMixedTurnFilter:
    def test_callback_and_new_distinct_person_coexist(self):
        """A continuity callback and a distinct new person both survive a turn."""
        catalogs = _catalog_with(
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        )
        callback = {
            "name": "Mara",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
        }
        joren = {
            "name": "Joren Veylin",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-joren-veylin",
        }
        kept = _apply_discovery_filter(catalogs, [callback, joren])
        assert callback in kept
        assert joren in kept
        assert len(kept) == 2


# ---------------------------------------------------------------------------
# Scenario 4 â€” unvalidated / unresolvable existing_id must NOT bypass #398
# (iteration-3 HIGH regression: the guard trusted model-supplied ids).
# ---------------------------------------------------------------------------

class TestUnresolvableExistingIdRejected:
    def test_named_fragment_with_invalid_existing_id_dropped(self):
        """The model claims a bogus existing_id for a bare compound fragment.

        Catalog has 'item-frost-precision' ("Frost Precision").  The LLM emits a
        bare 'Precision' with is_new=False + existing_id='item-precision' (an id
        that does NOT exist).  The guard must NOT spare it on the unvalidated id
        â€” it is still a compound-term fragment and must be dropped (#524).
        """
        catalogs = {
            "items.json": [
                {"id": "item-frost-precision", "name": "Frost Precision", "type": "item"}
            ]
        }
        bogus = {
            "name": "Precision",
            "type": "item",
            "is_new": False,
            "existing_id": "item-precision",
        }
        kept = _apply_discovery_filter(catalogs, [bogus])
        assert kept == []

    def test_named_fragment_with_valid_existing_id_kept(self):
        """The companion valid case still survives (no over-rejection)."""
        catalogs = {
            "items.json": [
                {"id": "item-frost-precision", "name": "Frost Precision", "type": "item"}
            ]
        }
        callback = {
            "name": "Precision",
            "type": "item",
            "is_new": False,
            "existing_id": "item-frost-precision",
        }
        kept = _apply_discovery_filter(catalogs, [callback])
        assert kept == [callback]


# ---------------------------------------------------------------------------
# Compact discovery-entry expansion fail-closed (#524): an unresolvable
# existing_id must be rejected, never converted into a name=raw-id merge task.
# ---------------------------------------------------------------------------

class TestCompactExpansionFailClosed:
    def test_compact_unresolvable_existing_id_dropped(self):
        """Compact {existing_id, confidence} with no matching catalog id -> dropped."""
        catalogs = {
            "items.json": [
                {"id": "item-frost-precision", "name": "Frost Precision", "type": "item"}
            ]
        }
        compact = {"existing_id": "item-precision", "confidence": 0.9}
        expanded, count, dropped = _expand_compact_discovery_entries(
            [compact], catalogs
        )
        # Rejected: not converted into a name=raw-id + is_new=False merge task.
        assert expanded == []
        assert count == 0
        assert dropped == ["item-precision"]

    def test_compact_resolvable_existing_id_expanded(self):
        """A compact callback to a real catalog id is expanded in place and kept."""
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        compact = {"existing_id": "char-mara-veylin", "confidence": 0.9}
        expanded, count, dropped = _expand_compact_discovery_entries(
            [compact], catalogs
        )
        assert count == 1
        assert dropped == []
        assert len(expanded) == 1
        assert expanded[0]["name"] == "Mara Veylin"
        assert expanded[0]["is_new"] is False


# ---------------------------------------------------------------------------
# Scenario 5 â€” a FULL named record with an unresolvable existing_id must be
# validated at the single uniform chokepoint in `_run_discovery_phase`, so it
# never becomes a detail/merge task on EITHER the sequential or the batch path
# (iteration-4 HIGH: a multi-word named bogus existing_id bypassed both the
# compact-expansion drop and the #398 compound-fragment guard, #524).
# ---------------------------------------------------------------------------

class _FakeDiscoveryLLM:
    """Minimal LLMClient stand-in that returns a fixed discovery payload.

    `_run_discovery_phase` only needs `extract_json`, plus the attributes the
    bounded-context/prompt builders read (`context_length`, `config`,
    `max_tokens`).  A fresh copy of each entity is returned per call so the
    function's in-place provenance mutation never leaks back into the fixture.
    """

    def __init__(self, entities):
        self._entities = entities
        self.context_length = None
        self.config = {}
        self.max_tokens = 2000

    def extract_json(self, **kwargs):
        return {"entities": [dict(e) for e in self._entities]}


def _make_turn():
    return {
        "turn_id": "turn-100",
        "speaker": "DM",
        "text": "Mara Baker greets the party at the gate.",
    }


def _build_entity_tasks(qualified, catalogs):
    """Mirror the SHARED `_entity_tasks` building loop in `extract_and_merge`.

    Both the sequential (solo) and batch detail/merge paths consume this exact
    list, so it is the faithful test surface for "did this record become a
    detail/merge task".
    """
    tasks = []
    for entity_ref in qualified:
        entity_id = get_entity_id(entity_ref)
        if not entity_id:
            continue
        stem = _strip_any_prefix(entity_id)
        if stem.lower() in _GENERIC_STEMS:
            continue
        current_entry = None
        if not entity_ref.get("is_new", True):
            result = find_entity_by_id(catalogs, entity_id)
            if result:
                _, current_entry = result
        tasks.append((entity_ref, current_entry))
    return tasks


def _all_task_ids(entity_tasks, catalogs):
    """Flatten the solo + batch task partition into the set of task ids.

    Exercises BOTH paths: batching OFF (the A/B control -> all solo) and ON
    (the lower-salience tail is grouped).  A record that is not in `qualified`
    can appear in NEITHER partition regardless of config.
    """
    ids: set[str] = set()
    batch_on = {
        "context_optimizations": {
            "batch_entity_detail": {
                "enabled": True,
                "batch_size": 2,
                "high_confidence_threshold": 0.85,
            }
        }
    }
    for cfg in (None, batch_on):
        solo, groups = _partition_detail_tasks(entity_tasks, cfg)
        flattened = list(solo) + [t for group in groups for t in group]
        for ref, _entry in flattened:
            ids.add(get_entity_id(ref))
    return ids


class TestNamedUnresolvableExistingIdFailsClosed:
    def test_named_multiword_bogus_existing_id_never_becomes_a_task(self):
        """`{name:"Mara Baker", is_new:false, existing_id:"char-mara-baker"}`.

        The catalog has NO `char-mara-baker`.  The record has a name (so compact
        expansion skips it) and a multi-word name (so the #398 guard skips it).
        The uniform existing_id validation must drop it BEFORE it can become a
        detail/merge task on EITHER the sequential or the batch path (#524).
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        bogus = {
            "name": "Mara Baker",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-baker",
            "confidence": 0.95,
        }
        llm = _FakeDiscoveryLLM([bogus])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        # Dropped from the qualified set, logged with the fail-closed reason.
        assert result["qualified"] == []
        reasons = {f["reason"] for f in result["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons
        dropped_ids = {f["id"] for f in result["discovery_filtered"]
                       if f["reason"] == "unresolvable_existing_id"}
        assert "char-mara-baker" in dropped_ids

        # Never reaches a detail/merge task on EITHER path.
        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert entity_tasks == []
        assert "char-mara-baker" not in _all_task_ids(entity_tasks, catalogs)

    def test_valid_named_existing_id_still_produces_existing_task(self):
        """A resolvable named existing_id is NOT over-rejected (no entity loss).

        It survives discovery and produces the correct EXISTING-entity task
        (snapshotting the catalog entry) on both the sequential and batch paths.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        callback = {
            "name": "Mara Veylin",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
            "confidence": 0.95,
        }
        llm = _FakeDiscoveryLLM([callback])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        assert len(result["qualified"]) == 1
        assert result["qualified"][0]["existing_id"] == "char-mara-veylin"
        assert all(f["reason"] != "unresolvable_existing_id"
                   for f in result["discovery_filtered"])

        # Produces an existing-entity task (current_entry resolved) on both paths.
        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert len(entity_tasks) == 1
        ref, current_entry = entity_tasks[0]
        assert get_entity_id(ref) == "char-mara-veylin"
        assert current_entry is not None
        assert current_entry["name"] == "Mara Veylin"
        assert "char-mara-veylin" in _all_task_ids(entity_tasks, catalogs)

    def test_bogus_existing_id_with_valid_new_proposal_proceeds_as_new(self):
        """`is_new=true` + valid `proposed_id` + bogus `existing_id` -> kept as new.

        The unresolvable reference is cleared (not dropped); the record proceeds
        as a genuinely new entity keyed on its proposed_id.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        new_with_bogus = {
            "name": "Joren Baker",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-joren-baker",
            "existing_id": "char-joren-baker-typo",
            "confidence": 0.9,
        }
        llm = _FakeDiscoveryLLM([new_with_bogus])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        assert len(result["qualified"]) == 1
        kept = result["qualified"][0]
        assert kept["existing_id"] is None
        assert kept["proposed_id"] == "char-joren-baker"

        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert len(entity_tasks) == 1
        ref, current_entry = entity_tasks[0]
        # New entity -> id resolves to proposed_id, no existing snapshot.
        assert get_entity_id(ref) == "char-joren-baker"
        assert current_entry is None
        assert "char-joren-baker" in _all_task_ids(entity_tasks, catalogs)

    def test_bogus_existing_id_with_colliding_proposed_id_dropped(self):
        """`is_new=true` + `proposed_id` COLLIDING with a real id + bogus
        `existing_id` -> DROPPED (fail closed), never rerouted or merged.

        iteration-5 introduced a collision-reroute (clear bogus existing_id,
        set ``existing_id = proposed_id``) that the adversarial caught as a NEW
        false-merge: a genuinely-new entity carrying a bad colliding proposed_id
        would be rerouted onto the collided catalog entry and could rename it
        downstream.  The ambiguous collision (unresolvable existing_id +
        proposed_id duplicating a real id) is now dropped: proceed-as-new reuses
        a colliding id (corrupts) and reroute-as-existing false-merges (corrupts),
        so dropping the rare malformed record is the only safe choice.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        collision = {
            "name": "Mara Veylin",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-mara-veylin",
            "existing_id": "char-mara-typo",
            "confidence": 0.9,
        }
        llm = _FakeDiscoveryLLM([collision])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        # Dropped, not rerouted -> never reaches a task.
        assert result["qualified"] == []
        reasons = {f["reason"] for f in result["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons

        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert entity_tasks == []
        assert "char-mara-veylin" not in _all_task_ids(entity_tasks, catalogs)

    def test_new_entity_colliding_proposed_id_does_not_false_merge(self):
        """A genuinely-NEW entity ("Mara Baker") that emits a bad colliding
        proposed_id (`char-mara-veylin`, an existing DIFFERENT person) must be
        DROPPED, and the collided catalog entry must remain UNCHANGED.

        This is the exact adversarial regression iteration-5's collision-reroute
        introduced: rerouting "Mara Baker" onto ``char-mara-veylin`` would let
        ``merge_entity`` rename/overwrite "Mara Veylin" (the catalog_merger name
        guard only blocks ZERO-overlap names, and "Mara Baker"/"Mara Veylin"
        share "Mara").  Fail closed instead â€” no identity corruption.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        new_colliding = {
            "name": "Mara Baker",
            "type": "character",
            "is_new": True,
            "proposed_id": "char-mara-veylin",
            "existing_id": "char-mara-typo",
            "confidence": 0.9,
        }
        llm = _FakeDiscoveryLLM([new_colliding])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        # Dropped, not false-merged.
        assert result["qualified"] == []
        reasons = {f["reason"] for f in result["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons

        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert entity_tasks == []
        # The collided catalog entry is UNCHANGED â€” not renamed to "Mara Baker".
        survivor = catalogs["characters.json"][0]
        assert survivor["id"] == "char-mara-veylin"
        assert survivor["name"] == "Mara Veylin"

    def test_new_entity_missing_proposed_id_with_bogus_existing_id_dropped(self):
        """`is_new=true` + MISSING proposed_id + bogus existing_id -> dropped.

        The escape hatch requires a valid, non-colliding proposed_id; without
        one the record fails closed (#4) and never becomes a task.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        bogus = {
            "name": "Joren Baker",
            "type": "character",
            "is_new": True,
            "existing_id": "char-joren-baker-typo",
            "confidence": 0.9,
        }
        llm = _FakeDiscoveryLLM([bogus])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        assert result["qualified"] == []
        reasons = {f["reason"] for f in result["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons
        entity_tasks = _build_entity_tasks(result["qualified"], catalogs)
        assert entity_tasks == []
        assert "char-joren-baker-typo" not in _all_task_ids(entity_tasks, catalogs)

    def test_new_entity_unprefixed_proposed_id_with_bogus_existing_id_dropped(self):
        """`is_new=true` + malformed (no typed-prefix) proposed_id + bogus
        existing_id -> dropped.

        A proposed_id that carries no recognized type prefix is not a valid new
        id, so the escape hatch fails closed (#4).  ``type`` is omitted so the
        upstream prefix auto-fix cannot rescue the malformed id.
        """
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        bogus = {
            "name": "Joren Baker",
            "is_new": True,
            "proposed_id": "joren-baker",  # no typed prefix; no type to fix it
            "existing_id": "char-joren-baker-typo",
            "confidence": 0.9,
        }
        llm = _FakeDiscoveryLLM([bogus])
        result = _run_discovery_phase(_make_turn(), catalogs, llm)

        assert result["qualified"] == []
        reasons = {f["reason"] for f in result["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons


# ---------------------------------------------------------------------------
# #1 â€” defense-in-depth: the prefetch/batch path consumes
# ``prefetched_discovery["qualified"]`` directly at the ``extract_and_merge``
# ingress.  The same ``_validate_existing_ids`` chokepoint must run there too
# so a bogus named multi-word existing_id cannot bypass validation via the
# batch route (the adversarial ``prefetch_bypass`` scenario).
# ---------------------------------------------------------------------------

def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_pipeline_llm():
    """Stub LLM that satisfies every phase ``extract_and_merge`` runs.

    Detail returns a valid char-player entry; relationships/events return
    empty.  Discovery is never called on the prefetch path.
    """
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        low = system_prompt.lower()
        if "detail" in low:
            return {"entity": {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-100",
                "last_updated_turn": "turn-100",
            }}
        if "relationship" in low:
            return {"relationships": []}
        if "event" in low:
            return {"events": []}
        return {"entities": []}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


class TestPrefetchIngressValidation:
    """The prefetched ``qualified`` list is re-validated at the ingress (#1)."""

    def test_validate_existing_ids_drops_bogus_named_record(self):
        """Unit: a full, named, multi-word record with an unresolvable
        existing_id is dropped by the reusable ``_validate_existing_ids``."""
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        bogus = {
            "name": "Mara Baker",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-baker",
            "confidence": 0.95,
        }
        validated, dropped = _validate_existing_ids([bogus], catalogs)
        assert validated == []
        assert {d["id"] for d in dropped} == {"char-mara-baker"}
        assert all(d["reason"] == "unresolvable_existing_id" for d in dropped)

    def test_validate_existing_ids_is_idempotent_on_clean_records(self):
        """Re-validating already-clean records is a no-op (idempotent)."""
        catalogs = {
            "characters.json": [
                {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
            ]
        }
        clean = {
            "name": "Mara Veylin",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
            "confidence": 0.95,
        }
        once, dropped1 = _validate_existing_ids([clean], catalogs)
        twice, dropped2 = _validate_existing_ids(once, catalogs)
        assert dropped1 == [] and dropped2 == []
        assert twice == once == [clean]

    def test_prefetch_bypass_bogus_id_reaches_no_task(self, monkeypatch):
        """End-to-end: a bogus named existing_id smuggled through
        ``prefetched_discovery`` is dropped at the ingress and never minted.

        The batch/prefetch path consumes ``prefetched_discovery["qualified"]``
        directly; the ingress re-validation must drop the bogus record so it
        reaches NO detail/merge task and is NEVER appended to the catalog.
        """
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        catalogs = _fresh_catalogs()
        catalogs["characters.json"] = [
            {"id": "char-mara-veylin", "name": "Mara Veylin", "type": "character"}
        ]
        bogus = {
            "name": "Mara Baker",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-baker",
            "confidence": 0.95,
        }
        prefetched = {
            "qualified": [bogus],
            "turn_failed": False,
            "discovery_proposals": [{
                "name": "Mara Baker", "is_new": False,
                "proposed_id": None, "existing_id": "char-mara-baker",
                "confidence": 0.95,
            }],
            "discovery_filtered": [],
            "phase_log": {"discovery_ok": True, "discovery_error": None},
            "discovery_sys_tmpl": None,
            "discovery_user_prompt": None,
        }
        llm = _make_pipeline_llm()
        turn = {"turn_id": "turn-100", "speaker": "DM",
                "text": "Mara Baker greets the party at the gate."}

        updated, _events, _failed, log = se.extract_and_merge(
            turn, catalogs, [], llm, min_confidence=0.6,
            prefetched_discovery=prefetched,
        )

        # The bogus id was dropped at the ingress and logged.
        reasons = {f["reason"] for f in log["discovery_filtered"]}
        assert "unresolvable_existing_id" in reasons
        dropped_ids = {f["id"] for f in log["discovery_filtered"]
                       if f["reason"] == "unresolvable_existing_id"}
        assert "char-mara-baker" in dropped_ids

        # It was NEVER appended to any catalog (no bogus entity minted).
        all_ids = {e.get("id") for ents in updated.values() for e in ents}
        assert "char-mara-baker" not in all_ids
