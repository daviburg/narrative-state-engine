"""Unit tests for relationship-demotion shadow logging (#495 step 1, epic #477).

MEASUREMENT-ONLY shadow. Covers:
  1. flag-OFF byte-identical: no artifact, seam output unchanged, strict-bool gate
  2. flag-ON schema + required fields, would_demote is bool, seam in {A, B}
  3. mention_source distinction (real_mention vs injected_floor vs none)
  4. would_demote == the Site B discrete tier-4-OMIT scoring
  5. records go to relationship-demotion-shadow.jsonl, NOT extraction-log.jsonl
  6. staleness == current_turn - last_updated_turn; core_degree from the index
"""

import json
import os
import sys

from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


_REQUIRED_FIELDS = (
    "turn", "owner", "target_id", "type", "status", "staleness",
    "core_degree", "mention_source", "would_demote", "seam",
)


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_rel(target_id, rel_type="ally", status="active",
              last_updated_turn="turn-040", source_id="char-player"):
    return {
        "source_id": source_id,
        "target_id": target_id,
        "type": rel_type,
        "status": status,
        "last_updated_turn": last_updated_turn,
        "current_relationship": "test",
    }


# ---------------------------------------------------------------------------
# 1. Gate helper — strict bool parse (mirrors the other measurement gates)
# ---------------------------------------------------------------------------

class TestGateHelper:
    def test_true_enables(self):
        cfg = {"context_optimizations": {"relationship_demotion_shadow": True}}
        assert se._relationship_demotion_shadow_enabled(cfg) is True

    def test_false_disables(self):
        cfg = {"context_optimizations": {"relationship_demotion_shadow": False}}
        assert se._relationship_demotion_shadow_enabled(cfg) is False

    def test_missing_key_disables(self):
        assert se._relationship_demotion_shadow_enabled({"context_optimizations": {}}) is False

    def test_missing_block_disables(self):
        assert se._relationship_demotion_shadow_enabled({}) is False

    def test_none_config_disables(self):
        assert se._relationship_demotion_shadow_enabled(None) is False

    def test_truthy_non_bool_disabled(self):
        # Strict ``is True``: truthy non-bools must NOT enable the gate.
        for val in ("true", "True", 1, [1], {"x": 1}):
            cfg = {"context_optimizations": {"relationship_demotion_shadow": val}}
            assert se._relationship_demotion_shadow_enabled(cfg) is False, val

    def test_malformed_block_disabled(self):
        # context_optimizations not a dict must not crash and must stay OFF.
        assert se._relationship_demotion_shadow_enabled(
            {"context_optimizations": ["nope"]}
        ) is False


# ---------------------------------------------------------------------------
# 2 + 3 + 4 + 6. Pure record computation
# ---------------------------------------------------------------------------

class TestComputeRecords:
    def test_schema_all_fields_present(self):
        rels_by_owner = {"char-player": [_make_rel("char-mentor")]}
        recs = se._compute_relationship_demotion_shadow_records(
            rels_by_owner,
            tier_mentioned_ids={"char-player", "char-mentor"},
            real_mentioned_ids={"char-mentor"},
            current_turn_num=100,
            turn_id="turn-100",
            seam="B",
            config=None,
            rel_index=None,
        )
        assert len(recs) == 1
        rec = recs[0]
        for field in _REQUIRED_FIELDS:
            assert field in rec, field
        assert isinstance(rec["would_demote"], bool)
        assert rec["seam"] in ("A", "B")
        assert rec["turn"] == "turn-100"
        assert rec["owner"] == "char-player"
        assert rec["target_id"] == "char-mentor"

    def test_seam_label_passthrough(self):
        for seam in ("A", "B"):
            recs = se._compute_relationship_demotion_shadow_records(
                {"char-player": [_make_rel("char-mentor")]},
                tier_mentioned_ids=set(), real_mentioned_ids=set(),
                current_turn_num=50, turn_id="turn-050", seam=seam,
                config=None, rel_index=None,
            )
            assert recs[0]["seam"] == seam

    def test_mention_source_real_mention(self):
        # endpoint genuinely mentioned in the turn -> real_mention (beats floor)
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [_make_rel("char-mentor")]},
            tier_mentioned_ids={"char-player", "char-mentor"},
            real_mentioned_ids={"char-mentor"},
            current_turn_num=100, turn_id="turn-100", seam="A",
            config=None, rel_index=None,
        )
        assert recs[0]["mention_source"] == "real_mention"

    def test_mention_source_injected_floor(self):
        # PC relationship kept only by the floor (no real mention) -> injected_floor
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [_make_rel("char-stranger")]},
            tier_mentioned_ids={"char-player"},
            real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="A",
            config=None, rel_index=None,
        )
        assert recs[0]["mention_source"] == "injected_floor"

    def test_mention_source_none(self):
        # non-PC owner, no real mention -> none
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-mentor": [_make_rel("char-stranger", source_id="char-mentor")]},
            tier_mentioned_ids=set(),
            real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs[0]["mention_source"] == "none"

    def test_would_demote_equals_tier4(self):
        # Neither endpoint mentioned -> seam-B scoring assigns tier 4 (OMIT).
        rel = _make_rel("char-stranger")
        tier = se._score_relationship_tier(
            rel, "char-player", set(), 100, False, frozenset(),
        )
        assert tier == 4
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [rel]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs[0]["would_demote"] is True

    def test_would_not_demote_when_mentioned(self):
        # Both endpoints mentioned -> tier 1 -> not demoted.
        rel = _make_rel("char-mentor")
        mentioned = {"char-player", "char-mentor"}
        tier = se._score_relationship_tier(
            rel, "char-player", mentioned, 100, False, frozenset(),
        )
        assert tier == 1
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [rel]},
            tier_mentioned_ids=mentioned, real_mentioned_ids={"char-mentor"},
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs[0]["would_demote"] is False

    def test_staleness_computed(self):
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [_make_rel("char-mentor", last_updated_turn="turn-040")]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs[0]["staleness"] == 60

    def test_staleness_null_when_unparseable(self):
        rel = _make_rel("char-mentor")
        del rel["last_updated_turn"]
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [rel]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs[0]["staleness"] is None

    def test_core_degree_from_index(self):
        rel_index = {
            "char-mentor": {"forward": [{}], "reverse": [{}, {}]},
        }
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [_make_rel("char-mentor")]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=rel_index,
        )
        assert recs[0]["core_degree"] == 3

    def test_core_degree_null_when_absent(self):
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": [_make_rel("char-stranger")]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index={"char-mentor": {"forward": [], "reverse": []}},
        )
        assert recs[0]["core_degree"] is None

    def test_empty_owner_rels_skipped(self):
        recs = se._compute_relationship_demotion_shadow_records(
            {"char-player": []},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert recs == []

    def test_read_only_does_not_mutate_rel(self):
        rel = _make_rel("char-mentor")
        snapshot = json.dumps(rel, sort_keys=True)
        se._compute_relationship_demotion_shadow_records(
            {"char-player": [rel]},
            tier_mentioned_ids=set(), real_mentioned_ids=set(),
            current_turn_num=100, turn_id="turn-100", seam="B",
            config=None, rel_index=None,
        )
        assert json.dumps(rel, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# 5. Appender — separate artifact path
# ---------------------------------------------------------------------------

class TestAppender:
    def test_writes_to_shadow_file_not_extraction_log(self, tmp_path):
        fw = str(tmp_path)
        records = [
            {"turn": "turn-100", "owner": "char-player", "seam": "A"},
            {"turn": "turn-100", "owner": "char-player", "seam": "B"},
        ]
        se._append_relationship_demotion_shadow(fw, records)
        shadow = os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        assert os.path.isfile(shadow)
        assert se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME == "relationship-demotion-shadow.jsonl"
        # NOT the extraction log
        assert not os.path.isfile(os.path.join(fw, "extraction-log.jsonl"))
        lines = [json.loads(ln) for ln in open(shadow, encoding="utf-8") if ln.strip()]
        assert len(lines) == 2
        assert {ln["seam"] for ln in lines} == {"A", "B"}

    def test_noop_on_empty(self, tmp_path):
        fw = str(tmp_path)
        se._append_relationship_demotion_shadow(fw, [])
        assert not os.path.isfile(
            os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        )

    def test_append_accumulates(self, tmp_path):
        fw = str(tmp_path)
        se._append_relationship_demotion_shadow(fw, [{"a": 1}])
        se._append_relationship_demotion_shadow(fw, [{"a": 2}])
        shadow = os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        lines = [json.loads(ln) for ln in open(shadow, encoding="utf-8") if ln.strip()]
        assert [ln["a"] for ln in lines] == [1, 2]


# ---------------------------------------------------------------------------
# 1b. Seam byte-identity — the flag must not change Site A / Site B output
# ---------------------------------------------------------------------------

_CFG_OFF = {"context_optimizations": {"relationship_demotion_shadow": False,
                                      "relationship_type_tiering": True,
                                      "pc_rel_volatile_tail_cap": 10}}
_CFG_ON = {"context_optimizations": {"relationship_demotion_shadow": True,
                                     "relationship_type_tiering": True,
                                     "pc_rel_volatile_tail_cap": 10}}


def _pc_entry_with_rels():
    return {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "identity": "The hero.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-100",
        "relationships": [
            _make_rel("char-mentor", rel_type="mentorship", last_updated_turn="turn-090"),
            _make_rel("char-stranger", rel_type="ally", last_updated_turn="turn-010"),
            _make_rel("char-rival", rel_type="adversarial", last_updated_turn="turn-005"),
        ],
    }


class TestSeamByteIdentical:
    def test_site_a_flag_does_not_alter_seam_output(self):
        """The shadow flag must not change what seam A serializes.

        Asserts a golden value (the seam actually emits the PC relationship
        block) so a regression that empties the seam can't pass by matching
        two empty strings, then asserts flag-ON == flag-OFF.
        """
        entry = _pc_entry_with_rels()
        off = se._format_prior_entity_context(
            dict(entry), config=_CFG_OFF, current_turn_num=100,
        )
        on = se._format_prior_entity_context(
            dict(entry), config=_CFG_ON, current_turn_num=100,
        )
        # Golden: the seam serializes a non-empty block naming a known endpoint.
        assert off, "seam A produced empty output — golden expectation broken"
        assert "char-mentor" in off
        assert off == on

    def test_site_b_flag_does_not_alter_seam_output(self):
        """The shadow flag must not change what seam B serializes.

        Asserts a golden value (the seam emits JSON containing the PC's
        relationships) before asserting flag-ON == flag-OFF, so the equality
        check can't trivially pass on empty output.
        """
        catalogs = _fresh_catalogs()
        catalogs["characters.json"] = [
            _pc_entry_with_rels(),
            {"id": "char-mentor", "name": "Mentor", "type": "character",
             "relationships": [_make_rel("char-player", source_id="char-mentor")]},
        ]
        ids = ["char-player", "char-mentor"]
        off = se._collect_existing_relationships(
            catalogs, ids, config=_CFG_OFF, turn_text="", current_turn_num=100,
        )
        on = se._collect_existing_relationships(
            catalogs, ids, config=_CFG_ON, turn_text="", current_turn_num=100,
        )
        # Golden: the seam emits non-empty JSON referencing the PC owner.
        assert off, "seam B produced empty output — golden expectation broken"
        assert "char-player" in off
        assert off == on


# ---------------------------------------------------------------------------
# 1c + 5. End-to-end wiring through extract_and_merge
# ---------------------------------------------------------------------------

def _make_stub_llm(config):
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = config

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        p = system_prompt.lower()
        if "detail" in p:
            return {"entity": {
                "id": "char-player", "name": "Player Character",
                "type": "character", "identity": "The hero.",
                "first_seen_turn": "turn-001", "last_updated_turn": "turn-100",
            }}
        if "relationship" in p:
            return {"relationships": []}
        if "event" in p:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _seeded_catalogs():
    catalogs = _fresh_catalogs()
    catalogs["characters.json"] = [
        {
            "id": "char-player", "name": "Player Character", "type": "character",
            "identity": "The hero.", "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-100",
            "relationships": [
                _make_rel("char-mentor", rel_type="mentorship", last_updated_turn="turn-090"),
                _make_rel("char-stranger", rel_type="ally", last_updated_turn="turn-005"),
            ],
        },
        {
            "id": "char-mentor", "name": "Mentor", "type": "character",
            "identity": "A wise mentor.", "first_seen_turn": "turn-002",
            "last_updated_turn": "turn-090",
            "relationships": [_make_rel("char-player", source_id="char-mentor",
                                        rel_type="mentorship")],
        },
    ]
    return catalogs


def _prefetched(qualified):
    return {
        "qualified": qualified,
        "turn_failed": False,
        "discovery_proposals": len(qualified),
        "discovery_filtered": 0,
        "phase_log": {"discovery_ok": True, "discovery_error": None},
        "discovery_sys_tmpl": "discovery template",
        "discovery_user_prompt": "discovery prompt",
    }


_QUALIFIED = [
    {"existing_id": "char-player", "name": "Player Character",
     "type": "character", "is_new": False, "confidence": 0.9},
    {"existing_id": "char-mentor", "name": "Mentor",
     "type": "character", "is_new": False, "confidence": 0.9},
]


class TestEndToEndWiring:
    def test_flag_off_writes_no_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        fw = str(tmp_path)
        llm = _make_stub_llm(
            {"context_optimizations": {"relationship_demotion_shadow": False}}
        )
        turn = {"turn_id": "turn-100", "speaker": "dm",
                "text": "The mentor speaks to the hero."}
        se.extract_and_merge(
            turn, _seeded_catalogs(), [], llm, min_confidence=0.6,
            framework_dir=fw, prefetched_discovery=_prefetched(_QUALIFIED),
        )
        assert not os.path.isfile(
            os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        )

    def test_flag_off_no_framework_dir_no_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        fw = str(tmp_path)
        # Flag ON but no framework_dir sink -> still zero work / no artifact.
        llm = _make_stub_llm(
            {"context_optimizations": {"relationship_demotion_shadow": True}}
        )
        turn = {"turn_id": "turn-100", "speaker": "dm",
                "text": "The mentor speaks."}
        se.extract_and_merge(
            turn, _seeded_catalogs(), [], llm, min_confidence=0.6,
            framework_dir=None, prefetched_discovery=_prefetched(_QUALIFIED),
        )
        assert not os.path.isfile(
            os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        )

    def test_flag_on_writes_artifact_both_seams(self, monkeypatch, tmp_path):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        fw = str(tmp_path)
        llm = _make_stub_llm(
            {"context_optimizations": {"relationship_demotion_shadow": True}}
        )
        turn = {"turn_id": "turn-100", "speaker": "dm",
                "text": "The Mentor speaks to the hero."}
        se.extract_and_merge(
            turn, _seeded_catalogs(), [], llm, min_confidence=0.6,
            framework_dir=fw, prefetched_discovery=_prefetched(_QUALIFIED),
        )
        shadow = os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        assert os.path.isfile(shadow)
        recs = [json.loads(ln) for ln in open(shadow, encoding="utf-8") if ln.strip()]
        assert recs, "expected shadow records"
        seams = {r["seam"] for r in recs}
        assert seams == {"A", "B"}
        for r in recs:
            for field in _REQUIRED_FIELDS:
                assert field in r, field
            assert isinstance(r["would_demote"], bool)
        # Separate artifact: extract_and_merge does not write extraction-log.jsonl.
        assert not os.path.isfile(os.path.join(fw, "extraction-log.jsonl"))

    def test_flag_on_records_mention_source_distinction(self, monkeypatch, tmp_path):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        fw = str(tmp_path)
        llm = _make_stub_llm(
            {"context_optimizations": {"relationship_demotion_shadow": True}}
        )
        # "Mentor" appears in the text (real mention); "char-stranger" does not.
        turn = {"turn_id": "turn-100", "speaker": "dm",
                "text": "The Mentor greets the hero warmly."}
        se.extract_and_merge(
            turn, _seeded_catalogs(), [], llm, min_confidence=0.6,
            framework_dir=fw, prefetched_discovery=_prefetched(_QUALIFIED),
        )
        shadow = os.path.join(fw, se._RELATIONSHIP_DEMOTION_SHADOW_FILENAME)
        recs = [json.loads(ln) for ln in open(shadow, encoding="utf-8") if ln.strip()]
        # The PC->mentor relationship should be a real_mention (Mentor named).
        pc_mentor = [r for r in recs if r["owner"] == "char-player"
                     and r["target_id"] == "char-mentor"]
        assert pc_mentor and all(r["mention_source"] == "real_mention" for r in pc_mentor)
        # The PC->stranger relationship is kept only by the floor.
        pc_stranger = [r for r in recs if r["owner"] == "char-player"
                       and r["target_id"] == "char-stranger"]
        assert pc_stranger and all(
            r["mention_source"] == "injected_floor" for r in pc_stranger
        )
