"""Tests for derive_planning_layer.py: state, evidence, and timeline derivation
from catalog data."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from derive_planning_layer import (
    _is_placeholder,
    _load_json,
    _next_seq,
    derive_all,
    derive_evidence,
    derive_state,
    derive_timeline,
    find_player_entity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def catalog_fixture(tmp_path):
    """Create a populated V2 catalog layout for testing."""
    session = tmp_path / "session"
    session.mkdir()
    (session / "transcript").mkdir()
    (session / "derived").mkdir()

    framework = tmp_path / "framework"
    catalogs = framework / "catalogs"

    # -- characters --
    chars_dir = catalogs / "characters"
    chars_dir.mkdir(parents=True)

    char_player = {
        "id": "char-player",
        "name": "Aelindra Starweaver",
        "type": "character",
        "identity": "An elven ranger exploring the frontier.",
        "current_status": "Resting at the village inn.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-042",
        "stable_attributes": {
            "race": {
                "value": "elf",
                "inference": False,
                "confidence": 1.0,
                "source_turn": "turn-001",
            },
            "class": {
                "value": "ranger",
                "inference": False,
                "confidence": 1.0,
                "source_turn": "turn-001",
            },
            "alignment": {
                "value": "neutral good",
                "inference": True,
                "confidence": 0.6,
                "source_turn": "turn-015",
            },
        },
        "volatile_state": {
            "condition": "healthy",
            "location": "loc-village-inn",
            "equipment": ["longbow", "leather armor", "hunting knife"],
            "last_updated_turn": "turn-042",
        },
        "relationships": [
            {
                "target_id": "char-innkeeper",
                "current_relationship": "friendly acquaintance",
                "type": "social",
                "status": "active",
                "confidence": 0.8,
                "first_seen_turn": "turn-010",
                "last_updated_turn": "turn-042",
            },
            {
                "target_id": "char-bandit-chief",
                "current_relationship": "hunted by",
                "type": "adversarial",
                "status": "active",
                "first_seen_turn": "turn-020",
                "last_updated_turn": "turn-038",
            },
            {
                "target_id": "char-old-hermit",
                "current_relationship": "former mentor",
                "type": "mentorship",
                "status": "dormant",
                "first_seen_turn": "turn-003",
                "last_updated_turn": "turn-005",
            },
        ],
    }
    (chars_dir / "char-player.json").write_text(
        json.dumps(char_player, indent=2), encoding="utf-8"
    )

    char_innkeeper = {
        "id": "char-innkeeper",
        "name": "Bran Oakheart",
        "type": "character",
        "identity": "The taciturn innkeeper of the Rusty Tankard.",
        "current_status": "Serving drinks behind the bar.",
        "first_seen_turn": "turn-010",
        "last_updated_turn": "turn-042",
        "stable_attributes": {
            "occupation": {
                "value": "innkeeper",
                "inference": False,
                "confidence": 1.0,
                "source_turn": "turn-010",
            },
        },
        "volatile_state": {
            "condition": "alert",
            "location": "loc-village-inn",
            "last_updated_turn": "turn-042",
        },
        "relationships": [],
    }
    (chars_dir / "char-innkeeper.json").write_text(
        json.dumps(char_innkeeper, indent=2), encoding="utf-8"
    )

    char_bandit = {
        "id": "char-bandit-chief",
        "name": "Red Mara",
        "type": "character",
        "identity": "Leader of the frontier bandits.",
        "current_status": "Location unknown; last seen heading east.",
        "first_seen_turn": "turn-020",
        "last_updated_turn": "turn-038",
        "stable_attributes": {},
        "volatile_state": {},
        "relationships": [],
    }
    (chars_dir / "char-bandit-chief.json").write_text(
        json.dumps(char_bandit, indent=2), encoding="utf-8"
    )

    chars_index = [
        {
            "id": "char-player",
            "name": "Aelindra Starweaver",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-042",
            "status_summary": "Resting at the village inn.",
            "active_relationship_count": 2,
        },
        {
            "id": "char-innkeeper",
            "name": "Bran Oakheart",
            "type": "character",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-042",
            "status_summary": "Serving drinks behind the bar.",
            "active_relationship_count": 0,
        },
        {
            "id": "char-bandit-chief",
            "name": "Red Mara",
            "type": "character",
            "first_seen_turn": "turn-020",
            "last_updated_turn": "turn-038",
            "status_summary": "Location unknown.",
            "active_relationship_count": 0,
        },
    ]
    (chars_dir / "index.json").write_text(
        json.dumps(chars_index, indent=2), encoding="utf-8"
    )

    # -- locations --
    locs_dir = catalogs / "locations"
    locs_dir.mkdir(parents=True)

    loc_inn = {
        "id": "loc-village-inn",
        "name": "the Rusty Tankard",
        "type": "location",
        "identity": "A weathered inn at the village crossroads.",
        "current_status": "Open for business, a few patrons inside.",
        "first_seen_turn": "turn-010",
        "last_updated_turn": "turn-042",
    }
    (locs_dir / "loc-village-inn.json").write_text(
        json.dumps(loc_inn, indent=2), encoding="utf-8"
    )

    loc_forest = {
        "id": "loc-dark-forest",
        "name": "the Dark Forest",
        "type": "location",
        "identity": "Dense woodland east of the village.",
        "current_status": "Bandit activity reported on the main trail.",
        "first_seen_turn": "turn-005",
        "last_updated_turn": "turn-035",
    }
    (locs_dir / "loc-dark-forest.json").write_text(
        json.dumps(loc_forest, indent=2), encoding="utf-8"
    )

    locs_index = [
        {
            "id": "loc-village-inn",
            "name": "the Rusty Tankard",
            "type": "location",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-042",
            "status_summary": "Open for business.",
            "active_relationship_count": 0,
        },
        {
            "id": "loc-dark-forest",
            "name": "the Dark Forest",
            "type": "location",
            "first_seen_turn": "turn-005",
            "last_updated_turn": "turn-035",
            "status_summary": "Bandit activity reported.",
            "active_relationship_count": 0,
        },
    ]
    (locs_dir / "index.json").write_text(
        json.dumps(locs_index, indent=2), encoding="utf-8"
    )

    # -- factions / items (empty) --
    for dirname in ("factions", "items"):
        d = catalogs / dirname
        d.mkdir(parents=True)
        (d / "index.json").write_text("[]", encoding="utf-8")

    # -- events --
    events = [
        {
            "id": "evt-001",
            "source_turns": ["turn-020"],
            "type": "encounter",
            "description": "Red Mara's bandits ambushed the trade caravan on the forest road.",
            "related_entities": ["char-bandit-chief", "loc-dark-forest"],
            "related_threads": ["plot-bandit-threat"],
        },
        {
            "id": "evt-002",
            "source_turns": ["turn-030"],
            "type": "discovery",
            "description": "Aelindra found a hidden cache of stolen goods near the old mill.",
            "related_entities": ["char-player"],
            "related_threads": ["plot-bandit-threat"],
        },
    ]
    (catalogs / "events.json").write_text(
        json.dumps(events, indent=2), encoding="utf-8"
    )

    # -- timeline --
    catalog_timeline = [
        {
            "id": "time-001",
            "source_turn": "turn-025",
            "type": "season_transition",
            "season": "late_summer",
            "description": "Transition to late summer",
            "raw_text": "As late summer settles over the frontier...",
        },
    ]
    (catalogs / "timeline.json").write_text(
        json.dumps(catalog_timeline, indent=2), encoding="utf-8"
    )

    # -- plot threads --
    plot_threads = [
        {
            "id": "plot-bandit-threat",
            "title": "The Bandit Threat",
            "status": "active",
            "open_questions": [
                "Where is Red Mara's hideout?",
                "Who is funding the bandits?",
            ],
        },
        {
            "id": "plot-ancient-ruins",
            "title": "The Ancient Ruins",
            "status": "active",
            "open_questions": [],
        },
        {
            "id": "plot-hermit-quest",
            "title": "The Hermit's Request",
            "status": "completed",
        },
    ]
    (catalogs / "plot-threads.json").write_text(
        json.dumps(plot_threads, indent=2), encoding="utf-8"
    )

    # -- anomalies (empty) --
    (catalogs / "anomalies.json").write_text("[]", encoding="utf-8")

    return {
        "session": str(session),
        "framework": str(framework),
        "catalog_dir": str(catalogs),
    }


@pytest.fixture
def empty_catalog_fixture(tmp_path):
    """Create an empty catalog structure (no per-entity directories)."""
    session = tmp_path / "session"
    session.mkdir()
    (session / "transcript").mkdir()
    (session / "derived").mkdir()

    framework = tmp_path / "framework"
    catalogs = framework / "catalogs"
    catalogs.mkdir(parents=True)

    for name in ("characters.json", "locations.json", "events.json",
                 "timeline.json", "plot-threads.json"):
        (catalogs / name).write_text("[]", encoding="utf-8")

    return {
        "session": str(session),
        "framework": str(framework),
        "catalog_dir": str(catalogs),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_placeholder_todo(self):
        assert _is_placeholder("TODO: Describe current world state from transcript.")

    def test_is_placeholder_unknown(self):
        assert _is_placeholder("Unknown")

    def test_is_placeholder_empty(self):
        assert _is_placeholder("")

    def test_is_placeholder_none(self):
        assert _is_placeholder(None)

    def test_is_placeholder_not_established(self):
        assert _is_placeholder("Not established")

    def test_is_placeholder_real_content(self):
        assert not _is_placeholder("The village of Thornhaven is quiet.")

    def test_next_seq_empty(self):
        assert _next_seq([], "ev") == 1

    def test_next_seq_existing(self):
        items = [{"id": "ev-001"}, {"id": "ev-003"}, {"id": "ev-002"}]
        assert _next_seq(items, "ev") == 4


# ---------------------------------------------------------------------------
# Player entity detection
# ---------------------------------------------------------------------------

class TestFindPlayerEntity:
    def test_find_by_exact_id(self, catalog_fixture):
        from build_context import load_indexes

        catalog_dir = catalog_fixture["catalog_dir"]
        _, id_lookup = load_indexes(catalog_dir)
        player = find_player_entity(catalog_dir, id_lookup)
        assert player is not None
        assert player["id"] == "char-player"

    def test_find_returns_none_without_player(self, catalog_fixture):
        """When no char-player exists, returns None."""
        from build_context import load_indexes

        catalog_dir = catalog_fixture["catalog_dir"]
        # Remove the player entity file and rebuild index
        chars_dir = os.path.join(catalog_dir, "characters")
        os.remove(os.path.join(chars_dir, "char-player.json"))
        index = _load_json(os.path.join(chars_dir, "index.json"), default=[])
        index = [e for e in index if e["id"] != "char-player"]
        with open(os.path.join(chars_dir, "index.json"), "w") as f:
            json.dump(index, f)

        _, id_lookup = load_indexes(catalog_dir)
        player = find_player_entity(catalog_dir, id_lookup)
        assert player is None


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------

class TestDeriveState:
    def test_populates_scaffold_state(self, catalog_fixture):
        """Scaffold state with TODOs gets populated from catalog data."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        # Write scaffold state
        derived = os.path.join(session_dir, "derived")
        scaffold = {
            "as_of_turn": "turn-042",
            "current_world_state": "TODO: Describe current world state from transcript.",
            "player_state": {
                "location": "Unknown",
                "condition": "Unknown",
                "inventory_notes": "Not established",
                "relationships_summary": "No NPCs contacted yet",
            },
            "known_constraints": [],
            "inferred_constraints": [],
            "opportunities": [],
            "risks": [],
            "active_threads": [],
        }
        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump(scaffold, f)

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        plot_threads = _load_json(
            os.path.join(catalog_dir, "plot-threads.json"), default=[]
        )

        state = derive_state(
            session_dir, catalog_dir, entities, id_lookup, plot_threads
        )

        # World state populated
        assert "TODO" not in state["current_world_state"]
        assert "Rusty Tankard" in state["current_world_state"]

        # Player state populated
        ps = state["player_state"]
        assert ps["location"] == "the Rusty Tankard"
        assert ps["condition"] == "healthy"
        assert "longbow" in ps["inventory_notes"]
        assert "Bran Oakheart" in ps["relationships_summary"]

        # Active threads from plot-threads.json
        assert "plot-bandit-threat" in state["active_threads"]
        assert "plot-ancient-ruins" in state["active_threads"]
        assert "plot-hermit-quest" not in state["active_threads"]

        # Known constraints from explicit attributes
        assert len(state["known_constraints"]) > 0
        assert any("race" in c and "elf" in c for c in state["known_constraints"])

        # Inferred constraints
        assert len(state["inferred_constraints"]) > 0
        assert any(
            "alignment" in ic["statement"]
            for ic in state["inferred_constraints"]
        )

        # Risks from adversarial relationships
        assert len(state["risks"]) > 0
        assert any("Red Mara" in r for r in state["risks"])

        # Opportunities from plot thread open questions
        assert len(state["opportunities"]) > 0
        assert any("hideout" in o for o in state["opportunities"])

    def test_preserves_manual_content(self, catalog_fixture):
        """Manually authored state fields are not overwritten."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]
        derived = os.path.join(session_dir, "derived")

        manual_state = {
            "as_of_turn": "turn-042",
            "current_world_state": "The village is peaceful under moonlight.",
            "player_state": {
                "location": "My custom location",
                "condition": "slightly tired",
                "inventory_notes": "Carrying important parcel",
                "relationships_summary": "Complex web of alliances",
            },
            "known_constraints": ["The bridge is out."],
            "inferred_constraints": [
                {"statement": "The mayor is hiding something", "confidence": 0.7, "source_turns": ["turn-030"]}
            ],
            "opportunities": ["Talk to the blacksmith"],
            "risks": ["Wolves on the road"],
            "active_threads": ["plot-custom-thread"],
        }
        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump(manual_state, f)

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        plot_threads = _load_json(
            os.path.join(catalog_dir, "plot-threads.json"), default=[]
        )

        state = derive_state(
            session_dir, catalog_dir, entities, id_lookup, plot_threads
        )

        # All manual content preserved
        assert state["current_world_state"] == "The village is peaceful under moonlight."
        assert state["player_state"]["location"] == "My custom location"
        assert state["player_state"]["condition"] == "slightly tired"
        assert state["known_constraints"] == ["The bridge is out."]
        assert state["risks"] == ["Wolves on the road"]
        assert state["active_threads"] == ["plot-custom-thread"]

    def test_updates_as_of_turn(self, catalog_fixture):
        """as_of_turn is updated from turns list."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]
        derived = os.path.join(session_dir, "derived")

        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump({"as_of_turn": "turn-001", "current_world_state": "X",
                        "player_state": {}, "active_threads": []}, f)

        _, id_lookup = load_indexes(catalog_dir)
        entities = []
        turns = [{"turn_id": "turn-050", "speaker": "dm", "text": "..."}]

        state = derive_state(
            session_dir, catalog_dir, entities, id_lookup, [], turns
        )
        assert state["as_of_turn"] == "turn-050"

    def test_dry_run_no_write(self, catalog_fixture):
        """Dry run does not create or modify files."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]
        state_path = os.path.join(session_dir, "derived", "state.json")

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)

        derive_state(
            session_dir, catalog_dir, entities, id_lookup, [], dry_run=True
        )
        assert not os.path.exists(state_path)


# ---------------------------------------------------------------------------
# Evidence derivation
# ---------------------------------------------------------------------------

class TestDeriveEvidence:
    def test_derives_from_events(self, catalog_fixture):
        """Catalog events become explicit_evidence entries."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        # Ensure empty evidence scaffold
        with open(os.path.join(session_dir, "derived", "evidence.json"), "w") as f:
            f.write("[]")

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])

        evidence = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )

        # Should have evidence from events + entity attributes + relationships
        assert len(evidence) > 0

        # Event-derived entries
        event_entries = [e for e in evidence if "catalog event" in (e.get("notes") or "")]
        assert len(event_entries) >= 2  # two events in fixture

        # Check first event
        ambush = [e for e in event_entries if "ambush" in e["statement"].lower()]
        assert len(ambush) == 1
        assert ambush[0]["classification"] == "explicit_evidence"
        assert ambush[0]["confidence"] == 1.0
        assert "turn-020" in ambush[0]["source_turns"]

    def test_derives_from_entity_attributes(self, catalog_fixture):
        """Entity attributes with inference flag become inference evidence."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        with open(os.path.join(session_dir, "derived", "evidence.json"), "w") as f:
            f.write("[]")

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])

        evidence = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )

        # Should have inferred alignment
        inferred = [e for e in evidence if e["classification"] == "inference"]
        assert len(inferred) >= 1
        alignment_ev = [e for e in inferred if "alignment" in e["statement"]]
        assert len(alignment_ev) == 1
        assert alignment_ev[0]["confidence"] == 0.6

    def test_derives_from_relationships(self, catalog_fixture):
        """Relationships with confidence < 1.0 become inference evidence."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        with open(os.path.join(session_dir, "derived", "evidence.json"), "w") as f:
            f.write("[]")

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])

        evidence = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )

        # Relationship with confidence 0.8 → inference
        rel_ev = [
            e for e in evidence
            if "entity relationship" in (e.get("notes") or "")
        ]
        assert len(rel_ev) >= 1
        assert any(
            "Bran Oakheart" in e["statement"] for e in rel_ev
        )

    def test_preserves_existing_evidence(self, catalog_fixture):
        """Existing manually authored evidence is preserved."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        existing = [
            {
                "id": "ev-001",
                "statement": "The innkeeper flinched when asked about the scholar.",
                "classification": "explicit_evidence",
                "confidence": 1.0,
                "source_turns": ["turn-010"],
                "related_entities": ["char-innkeeper"],
            }
        ]
        with open(os.path.join(session_dir, "derived", "evidence.json"), "w") as f:
            json.dump(existing, f)

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])

        evidence = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )

        # Manual entry preserved
        assert evidence[0]["statement"] == "The innkeeper flinched when asked about the scholar."
        # New entries added after
        assert len(evidence) > 1
        # IDs continue from existing max
        new_ids = [e["id"] for e in evidence[1:]]
        assert all(int(eid.split("-")[1]) >= 2 for eid in new_ids)

    def test_deduplication(self, catalog_fixture):
        """Running derive twice does not create duplicate entries."""
        from build_context import load_indexes

        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        with open(os.path.join(session_dir, "derived", "evidence.json"), "w") as f:
            f.write("[]")

        _, id_lookup = load_indexes(catalog_dir)
        from derive_planning_layer import _load_all_entities

        entities = _load_all_entities(catalog_dir, id_lookup)
        events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])

        # First run
        evidence1 = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )
        # Second run
        evidence2 = derive_evidence(
            session_dir, catalog_dir, entities, events, id_lookup
        )
        assert len(evidence1) == len(evidence2)


# ---------------------------------------------------------------------------
# Timeline derivation
# ---------------------------------------------------------------------------

class TestDeriveTimeline:
    def test_merges_catalog_into_session(self, catalog_fixture):
        """Catalog timeline entries are added to session timeline."""
        session_dir = catalog_fixture["session"]
        catalog_dir = catalog_fixture["catalog_dir"]

        # Write session-level timeline
        session_timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-010",
                "type": "season_transition",
                "season": "mid_summer",
                "description": "Transition to mid summer",
                "raw_text": "Summer deepens...",
            },
        ]
        with open(os.path.join(session_dir, "derived", "timeline.json"), "w") as f:
            json.dump(session_timeline, f)

        catalog_timeline = _load_json(
            os.path.join(catalog_dir, "timeline.json"), default=[]
        )

        result = derive_timeline(session_dir, catalog_timeline)

        # Should have both entries
        assert len(result) == 2
        # Sorted by turn number
        assert result[0]["source_turn"] == "turn-010"
        assert result[1]["source_turn"] == "turn-025"
        # IDs reassigned
        assert result[0]["id"] == "time-001"
        assert result[1]["id"] == "time-002"

    def test_deduplicates_by_key(self, catalog_fixture):
        """Entries with same (source_turn, type, season) are not duplicated."""
        session_dir = catalog_fixture["session"]

        session_timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-025",
                "type": "season_transition",
                "season": "late_summer",
                "description": "Summer wanes",
                "raw_text": "...",
            },
        ]
        with open(os.path.join(session_dir, "derived", "timeline.json"), "w") as f:
            json.dump(session_timeline, f)

        # Catalog has same entry
        catalog_timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-025",
                "type": "season_transition",
                "season": "late_summer",
                "description": "Transition to late summer",
                "raw_text": "As late summer settles...",
            },
        ]

        result = derive_timeline(session_dir, catalog_timeline)
        assert len(result) == 1

    def test_empty_catalog_preserves_session(self, catalog_fixture):
        """Empty catalog timeline leaves session timeline unchanged."""
        session_dir = catalog_fixture["session"]

        session_timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-005",
                "type": "season_transition",
                "season": "early_spring",
                "description": "Spring begins",
            },
        ]
        with open(os.path.join(session_dir, "derived", "timeline.json"), "w") as f:
            json.dump(session_timeline, f)

        result = derive_timeline(session_dir, [])
        assert len(result) == 1
        assert result[0]["season"] == "early_spring"


# ---------------------------------------------------------------------------
# derive_all orchestration
# ---------------------------------------------------------------------------

class TestDeriveAll:
    def test_full_pipeline(self, catalog_fixture):
        """derive_all populates state, evidence, and timeline."""
        session_dir = catalog_fixture["session"]
        framework_dir = catalog_fixture["framework"]

        # Write scaffolds
        derived = os.path.join(session_dir, "derived")
        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump({
                "as_of_turn": "turn-042",
                "current_world_state": "TODO: Describe current world state.",
                "player_state": {
                    "location": "Unknown",
                    "condition": "Unknown",
                    "inventory_notes": "Not established",
                    "relationships_summary": "No NPCs contacted yet",
                },
                "known_constraints": [],
                "inferred_constraints": [],
                "opportunities": [],
                "risks": [],
                "active_threads": [],
            }, f)
        with open(os.path.join(derived, "evidence.json"), "w") as f:
            f.write("[]")
        with open(os.path.join(derived, "timeline.json"), "w") as f:
            f.write("[]")

        result = derive_all(session_dir, framework_dir)

        assert result["state"].get("current_world_state") != "TODO: Describe current world state."
        assert len(result["evidence"]) > 0
        assert len(result["timeline"]) > 0

    def test_empty_catalogs_graceful(self, empty_catalog_fixture):
        """With empty catalogs, derive_all returns empty results gracefully."""
        session_dir = empty_catalog_fixture["session"]
        framework_dir = empty_catalog_fixture["framework"]

        result = derive_all(session_dir, framework_dir)

        assert result["state"] == {}
        assert result["evidence"] == []
        assert result["timeline"] == []

    def test_schema_compliance_state(self, catalog_fixture):
        """Derived state.json validates against schema."""
        session_dir = catalog_fixture["session"]
        framework_dir = catalog_fixture["framework"]

        derived = os.path.join(session_dir, "derived")
        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump({
                "as_of_turn": "turn-042",
                "current_world_state": "TODO: fill in",
                "player_state": {
                    "location": "Unknown",
                    "condition": "Unknown",
                    "inventory_notes": "Not established",
                    "relationships_summary": "No NPCs contacted yet",
                },
                "active_threads": [],
            }, f)
        with open(os.path.join(derived, "evidence.json"), "w") as f:
            f.write("[]")
        with open(os.path.join(derived, "timeline.json"), "w") as f:
            f.write("[]")

        result = derive_all(session_dir, framework_dir)

        state = result["state"]
        # Required fields present per state.schema.json
        assert "as_of_turn" in state
        assert "current_world_state" in state
        assert "player_state" in state
        assert "active_threads" in state
        # as_of_turn matches pattern
        assert state["as_of_turn"].startswith("turn-")

    def test_schema_compliance_evidence(self, catalog_fixture):
        """Derived evidence entries have all required fields."""
        session_dir = catalog_fixture["session"]
        framework_dir = catalog_fixture["framework"]

        derived = os.path.join(session_dir, "derived")
        with open(os.path.join(derived, "state.json"), "w") as f:
            json.dump({
                "as_of_turn": "turn-042",
                "current_world_state": "X",
                "player_state": {},
                "active_threads": [],
            }, f)
        with open(os.path.join(derived, "evidence.json"), "w") as f:
            f.write("[]")
        with open(os.path.join(derived, "timeline.json"), "w") as f:
            f.write("[]")

        result = derive_all(session_dir, framework_dir)

        for ev in result["evidence"]:
            assert "id" in ev
            assert "statement" in ev
            assert "classification" in ev
            assert ev["classification"] in (
                "explicit_evidence", "inference", "dm_bait", "player_hypothesis"
            )
            assert "confidence" in ev
            assert 0.0 <= ev["confidence"] <= 1.0
            assert "source_turns" in ev
            assert len(ev["source_turns"]) >= 1
