"""Tests for build_scene_graph.py: location index, turn activity index,
location connections, query helpers, and build_context.py integration."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from build_scene_graph import (
    build_location_connections,
    build_location_index,
    build_scene_graph,
    build_turn_activity,
    load_all_entities,
    load_scene_graph,
    parse_turn_number,
    query_active_in_turn_range,
    query_entities_at_location,
    query_nearby_from_index,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def catalog_fixture(tmp_path):
    """Create a V2 catalog layout with entities across multiple types."""
    framework = tmp_path / "framework"
    catalogs = framework / "catalogs"

    # Characters
    chars_dir = catalogs / "characters"
    chars_dir.mkdir(parents=True)

    char_player = {
        "id": "char-player",
        "name": "Fenouille",
        "type": "character",
        "identity": "Player character.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-050",
        "volatile_state": {
            "condition": "healthy",
            "location": "loc-camp",
            "last_updated_turn": "turn-050",
        },
        "relationships": [],
    }
    (chars_dir / "char-player.json").write_text(
        json.dumps(char_player, indent=2), encoding="utf-8"
    )

    char_elder = {
        "id": "char-elder",
        "name": "the elder",
        "type": "character",
        "identity": "Village elder.",
        "first_seen_turn": "turn-010",
        "last_updated_turn": "turn-045",
        "volatile_state": {
            "condition": "alert",
            "location": "loc-camp",
            "last_updated_turn": "turn-045",
        },
        "relationships": [],
    }
    (chars_dir / "char-elder.json").write_text(
        json.dumps(char_elder, indent=2), encoding="utf-8"
    )

    char_guard = {
        "id": "char-guard",
        "name": "Guard Kelvin",
        "type": "character",
        "identity": "Camp guard.",
        "first_seen_turn": "turn-015",
        "last_updated_turn": "turn-030",
        "volatile_state": {
            "condition": "patrolling",
            "location": "loc-forest",
            "last_updated_turn": "turn-030",
        },
        "relationships": [],
    }
    (chars_dir / "char-guard.json").write_text(
        json.dumps(char_guard, indent=2), encoding="utf-8"
    )

    chars_index = [
        {"id": "char-player", "name": "Fenouille", "type": "character",
         "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
         "status_summary": "Healthy at camp.", "active_relationship_count": 0},
        {"id": "char-elder", "name": "the elder", "type": "character",
         "first_seen_turn": "turn-010", "last_updated_turn": "turn-045",
         "status_summary": "Alert.", "active_relationship_count": 0},
        {"id": "char-guard", "name": "Guard Kelvin", "type": "character",
         "first_seen_turn": "turn-015", "last_updated_turn": "turn-030",
         "status_summary": "Patrolling.", "active_relationship_count": 0},
    ]
    (chars_dir / "index.json").write_text(
        json.dumps(chars_index, indent=2), encoding="utf-8"
    )

    # Locations
    locs_dir = catalogs / "locations"
    locs_dir.mkdir(parents=True)

    loc_camp = {
        "id": "loc-camp",
        "name": "the camp",
        "type": "location",
        "identity": "Forest clearing encampment.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-050",
        "relationships": [
            {
                "target_id": "loc-forest",
                "current_relationship": "surrounded by",
                "type": "other",
                "status": "active",
                "first_seen_turn": "turn-003",
            },
        ],
    }
    (locs_dir / "loc-camp.json").write_text(
        json.dumps(loc_camp, indent=2), encoding="utf-8"
    )

    loc_forest = {
        "id": "loc-forest",
        "name": "the forest",
        "type": "location",
        "identity": "Dense moonlit forest.",
        "first_seen_turn": "turn-003",
        "last_updated_turn": "turn-040",
        "relationships": [
            {
                "target_id": "loc-camp",
                "current_relationship": "surrounds",
                "type": "other",
                "status": "active",
                "first_seen_turn": "turn-003",
            },
            {
                "target_id": "loc-ruins",
                "current_relationship": "path leads to",
                "type": "other",
                "status": "dormant",
                "first_seen_turn": "turn-020",
            },
        ],
    }
    (locs_dir / "loc-forest.json").write_text(
        json.dumps(loc_forest, indent=2), encoding="utf-8"
    )

    loc_ruins = {
        "id": "loc-ruins",
        "name": "ancient ruins",
        "type": "location",
        "identity": "Crumbling stone ruins.",
        "first_seen_turn": "turn-020",
        "last_updated_turn": "turn-025",
        "relationships": [],
    }
    (locs_dir / "loc-ruins.json").write_text(
        json.dumps(loc_ruins, indent=2), encoding="utf-8"
    )

    locs_index = [
        {"id": "loc-camp", "name": "the camp", "type": "location",
         "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
         "status_summary": "Active.", "active_relationship_count": 1},
        {"id": "loc-forest", "name": "the forest", "type": "location",
         "first_seen_turn": "turn-003", "last_updated_turn": "turn-040",
         "status_summary": "Dark.", "active_relationship_count": 2},
        {"id": "loc-ruins", "name": "ancient ruins", "type": "location",
         "first_seen_turn": "turn-020", "last_updated_turn": "turn-025",
         "status_summary": "Quiet.", "active_relationship_count": 0},
    ]
    (locs_dir / "index.json").write_text(
        json.dumps(locs_index, indent=2), encoding="utf-8"
    )

    # Items
    items_dir = catalogs / "items"
    items_dir.mkdir(parents=True)

    item_sword = {
        "id": "item-sword",
        "name": "rusty sword",
        "type": "item",
        "identity": "An old rusty sword.",
        "first_seen_turn": "turn-005",
        "last_updated_turn": "turn-005",
        "volatile_state": {
            "location": "loc-camp",
        },
        "relationships": [],
    }
    (items_dir / "item-sword.json").write_text(
        json.dumps(item_sword, indent=2), encoding="utf-8"
    )
    (items_dir / "index.json").write_text("[]", encoding="utf-8")

    # Factions (empty)
    factions_dir = catalogs / "factions"
    factions_dir.mkdir(parents=True)
    (factions_dir / "index.json").write_text("[]", encoding="utf-8")

    return {
        "framework": str(framework),
        "catalogs": str(catalogs),
    }


# ---------------------------------------------------------------------------
# Test: parse_turn_number
# ---------------------------------------------------------------------------

class TestParseTurnNumber:
    def test_valid_turn(self):
        assert parse_turn_number("turn-045") == 45

    def test_zero_padded(self):
        assert parse_turn_number("turn-001") == 1

    def test_invalid(self):
        assert parse_turn_number("bad-id") == 0

    def test_empty(self):
        assert parse_turn_number("") == 0


# ---------------------------------------------------------------------------
# Test: load_all_entities
# ---------------------------------------------------------------------------

class TestLoadAllEntities:
    def test_loads_all_types(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        ids = {e["id"] for e in entities}
        assert "char-player" in ids
        assert "char-elder" in ids
        assert "loc-camp" in ids
        assert "item-sword" in ids

    def test_skips_index_files(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        # index.json should not appear as an entity
        for e in entities:
            assert "index" not in e.get("id", "")

    def test_empty_catalog(self, tmp_path):
        catalogs = tmp_path / "catalogs"
        catalogs.mkdir()
        entities = load_all_entities(str(catalogs))
        assert entities == []


# ---------------------------------------------------------------------------
# Test: build_location_index
# ---------------------------------------------------------------------------

class TestBuildLocationIndex:
    def test_groups_by_location(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        loc_names = {"loc-camp": "the camp", "loc-forest": "the forest"}
        index = build_location_index(entities, loc_names)

        assert "loc-camp" in index
        camp_ids = {e["id"] for e in index["loc-camp"]["entities"]}
        assert "char-player" in camp_ids
        assert "char-elder" in camp_ids
        assert "item-sword" in camp_ids

    def test_forest_entities(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        loc_names = {"loc-camp": "the camp", "loc-forest": "the forest"}
        index = build_location_index(entities, loc_names)

        assert "loc-forest" in index
        forest_ids = {e["id"] for e in index["loc-forest"]["entities"]}
        assert "char-guard" in forest_ids
        assert "char-player" not in forest_ids

    def test_location_name_resolved(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        loc_names = {"loc-camp": "the camp"}
        index = build_location_index(entities, loc_names)
        assert index["loc-camp"]["location_name"] == "the camp"

    def test_no_location_entities_excluded(self):
        entities = [
            {"id": "char-nomad", "name": "Nomad", "type": "character",
             "first_seen_turn": "turn-001", "volatile_state": {}},
        ]
        index = build_location_index(entities, {})
        assert index == {}

    def test_entities_sorted_by_id(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        loc_names = {"loc-camp": "the camp"}
        index = build_location_index(entities, loc_names)
        ids = [e["id"] for e in index["loc-camp"]["entities"]]
        assert ids == sorted(ids)

    def test_spatial_relationships_indexed(self):
        """Entities with spatial relationships to locations appear in location_index."""
        entities = [
            {
                "id": "char-wanderer",
                "name": "wanderer",
                "type": "character",
                "first_seen_turn": "turn-005",
                "last_updated_turn": "turn-010",
                "volatile_state": {"location": "near the fire"},
                "relationships": [
                    {
                        "target_id": "loc-tavern",
                        "current_relationship": "inside",
                        "type": "spatial",
                        "status": "active",
                        "last_updated_turn": "turn-010",
                    },
                ],
            },
        ]
        loc_names = {"loc-tavern": "The Tavern"}
        index = build_location_index(entities, loc_names)
        assert "loc-tavern" in index
        tavern_ids = {e["id"] for e in index["loc-tavern"]["entities"]}
        assert "char-wanderer" in tavern_ids

    def test_resolved_spatial_relationship_excluded(self):
        """Resolved spatial relationships should not place entity at location."""
        entities = [
            {
                "id": "char-departed",
                "name": "departed one",
                "type": "character",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-020",
                "volatile_state": {},
                "relationships": [
                    {
                        "target_id": "loc-village",
                        "current_relationship": "departed_from",
                        "type": "spatial",
                        "status": "resolved",
                        "last_updated_turn": "turn-020",
                    },
                ],
            },
        ]
        index = build_location_index(entities, {"loc-village": "the village"})
        assert "loc-village" not in index

    def test_spatial_deduplicates_with_volatile_state(self):
        """Entity at location via both volatile_state and spatial rel appears once."""
        entities = [
            {
                "id": "char-smith",
                "name": "blacksmith",
                "type": "character",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-005",
                "volatile_state": {"location": "loc-forge"},
                "relationships": [
                    {
                        "target_id": "loc-forge",
                        "current_relationship": "resides_at",
                        "type": "spatial",
                        "status": "active",
                        "last_updated_turn": "turn-005",
                    },
                ],
            },
        ]
        loc_names = {"loc-forge": "The Forge"}
        index = build_location_index(entities, loc_names)
        assert "loc-forge" in index
        forge_entries = index["loc-forge"]["entities"]
        forge_ids = [e["id"] for e in forge_entries]
        assert forge_ids.count("char-smith") == 1


# ---------------------------------------------------------------------------
# Test: build_turn_activity
# ---------------------------------------------------------------------------

class TestBuildTurnActivity:
    def test_records_first_seen(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        activity = build_turn_activity(entities)
        # char-player first_seen is turn-001
        assert "char-player" in activity["turn-001"]

    def test_records_last_updated(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        activity = build_turn_activity(entities)
        # char-player last_updated is turn-050
        assert "char-player" in activity["turn-050"]

    def test_multiple_entities_same_turn(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        activity = build_turn_activity(entities)
        # turn-050: char-player (last_updated) + loc-camp (last_updated)
        assert "char-player" in activity["turn-050"]
        assert "loc-camp" in activity["turn-050"]

    def test_sorted_by_turn(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        activity = build_turn_activity(entities)
        turns = list(activity.keys())
        turn_nums = [parse_turn_number(t) for t in turns]
        assert turn_nums == sorted(turn_nums)

    def test_empty_entities(self):
        assert build_turn_activity([]) == {}


# ---------------------------------------------------------------------------
# Test: build_location_connections
# ---------------------------------------------------------------------------

class TestBuildLocationConnections:
    def test_finds_connections(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        conns = build_location_connections(entities)

        # loc-camp <-> loc-forest should appear once (deduplicated)
        camp_forest = [c for c in conns
                       if {c["source"], c["target"]} == {"loc-camp", "loc-forest"}]
        assert len(camp_forest) == 1

    def test_deduplicates_bidirectional(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        conns = build_location_connections(entities)

        # Both loc-camp and loc-forest have reciprocal relationships
        # but should only appear once
        all_edges = [(c["source"], c["target"]) for c in conns]
        unique_edges = {tuple(sorted(e)) for e in all_edges}
        assert len(all_edges) == len(unique_edges)

    def test_dormant_connection_included(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        conns = build_location_connections(entities)

        # loc-forest -> loc-ruins is dormant
        forest_ruins = [c for c in conns
                        if {c["source"], c["target"]} == {"loc-forest", "loc-ruins"}]
        assert len(forest_ruins) == 1
        assert forest_ruins[0]["status"] == "dormant"

    def test_no_non_location_relationships(self, catalog_fixture):
        entities = load_all_entities(catalog_fixture["catalogs"])
        conns = build_location_connections(entities)

        for c in conns:
            assert c["source"].startswith("loc-")
            assert c["target"].startswith("loc-")

    def test_empty_entities(self):
        assert build_location_connections([]) == []


# ---------------------------------------------------------------------------
# Test: build_scene_graph (full pipeline)
# ---------------------------------------------------------------------------

class TestBuildSceneGraph:
    def test_produces_valid_structure(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])

        assert "as_of_turn" in sg
        assert "generated_at" in sg
        assert "location_index" in sg
        assert "turn_activity" in sg
        assert "location_connections" in sg
        assert "entity_count" in sg

    def test_as_of_turn_is_latest(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        assert sg["as_of_turn"] == "turn-050"

    def test_entity_count(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        # 3 characters + 3 locations + 1 item = 7
        assert sg["entity_count"] == 7

    def test_writes_output_file(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        output_path = os.path.join(catalog_fixture["catalogs"], "scene-graph.json")
        assert os.path.isfile(output_path)

        with open(output_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["entity_count"] == sg["entity_count"]

    def test_custom_output_path(self, catalog_fixture, tmp_path):
        output = str(tmp_path / "custom" / "graph.json")
        build_scene_graph(catalog_fixture["framework"], output_path=output)
        assert os.path.isfile(output)

    def test_empty_catalogs(self, tmp_path):
        framework = tmp_path / "framework"
        (framework / "catalogs").mkdir(parents=True)
        sg = build_scene_graph(str(framework))
        assert sg["entity_count"] == 0
        assert sg["location_index"] == {}
        assert sg["turn_activity"] == {}
        assert sg["location_connections"] == []


# ---------------------------------------------------------------------------
# Test: query helpers
# ---------------------------------------------------------------------------

class TestQueryEntitiesAtLocation:
    def test_returns_entities(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        result = query_entities_at_location(sg, "loc-camp")
        ids = {e["id"] for e in result}
        assert "char-player" in ids
        assert "char-elder" in ids

    def test_unknown_location(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        assert query_entities_at_location(sg, "loc-unknown") == []


class TestQueryActiveInTurnRange:
    def test_range_query(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        result = query_active_in_turn_range(sg, 1, 10)
        assert "char-player" in result  # first_seen turn-001
        assert "char-elder" in result   # first_seen turn-010
        assert "item-sword" in result   # first_seen/last_updated turn-005

    def test_narrow_range(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        result = query_active_in_turn_range(sg, 46, 50)
        assert "char-player" in result  # last_updated turn-050
        assert "char-guard" not in result  # last_updated turn-030

    def test_empty_range(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        result = query_active_in_turn_range(sg, 100, 200)
        assert result == set()


class TestQueryNearbyFromIndex:
    def test_nearby_excludes_scene(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        scene_ids = {"char-player", "loc-camp"}
        result = query_nearby_from_index(sg, scene_ids, "turn-050", 10)
        assert "char-player" not in result
        assert "char-elder" in result  # last_updated turn-045, within 10 of 50

    def test_nearby_respects_window(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        scene_ids = {"char-player"}
        result = query_nearby_from_index(sg, scene_ids, "turn-050", 5)
        # char-elder last_updated turn-045 → within 5 of 50
        assert "char-elder" in result
        # char-guard last_updated turn-030 → NOT within 5 of 50
        assert "char-guard" not in result

    def test_invalid_turn(self, catalog_fixture):
        sg = build_scene_graph(catalog_fixture["framework"])
        assert query_nearby_from_index(sg, set(), "bad-turn", 10) == []


# ---------------------------------------------------------------------------
# Test: load_scene_graph
# ---------------------------------------------------------------------------

class TestLoadSceneGraph:
    def test_loads_after_build(self, catalog_fixture):
        build_scene_graph(catalog_fixture["framework"])
        sg = load_scene_graph(catalog_fixture["framework"])
        assert sg is not None
        assert sg["entity_count"] == 7

    def test_returns_none_when_missing(self, tmp_path):
        assert load_scene_graph(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Test: schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_scene_graph_validates(self, catalog_fixture):
        """Scene graph output should validate against scene-graph.schema.json."""
        jsonschema = pytest.importorskip("jsonschema")

        sg = build_scene_graph(catalog_fixture["framework"])

        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "scene-graph.schema.json"
        )
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        jsonschema.validate(sg, schema)


# ---------------------------------------------------------------------------
# Test: build_context.py integration with scene graph
# ---------------------------------------------------------------------------

class TestBuildContextIntegration:
    """Test that build_context.py uses the scene graph for nearby lookups."""

    @pytest.fixture
    def integration_fixture(self, catalog_fixture, tmp_path):
        """Extend the catalog fixture with session/transcript for build_context."""
        session = tmp_path / "session"
        session.mkdir()
        transcript = session / "transcript"
        transcript.mkdir()
        derived = session / "derived"
        derived.mkdir()

        # Write a turn transcript that mentions char-player
        turn_path = transcript / "turn-050-dm.md"
        turn_path.write_text(
            "# turn-050 — DM\n\nFenouille surveys the camp.\n",
            encoding="utf-8",
        )

        # Build scene graph first
        build_scene_graph(catalog_fixture["framework"])

        return {
            "session": str(session),
            "framework": catalog_fixture["framework"],
            "catalogs": catalog_fixture["catalogs"],
            "transcript": str(transcript),
        }

    def test_nearby_uses_scene_graph(self, integration_fixture):
        from build_context import build_context

        ctx = build_context(
            session_dir=integration_fixture["session"],
            turn_id="turn-050",
            framework_dir=integration_fixture["framework"],
            nearby_turns=10,
            use_scene_graph=True,
        )
        nearby_ids = {e["id"] for e in ctx.get("nearby_entities_summary", [])}
        # char-elder was last_updated at turn-045, within 10 of turn-050
        assert "char-elder" in nearby_ids

    def test_no_scene_graph_fallback(self, integration_fixture):
        from build_context import build_context

        ctx = build_context(
            session_dir=integration_fixture["session"],
            turn_id="turn-050",
            framework_dir=integration_fixture["framework"],
            nearby_turns=10,
            use_scene_graph=False,
        )
        nearby_ids = {e["id"] for e in ctx.get("nearby_entities_summary", [])}
        # Should still work via the original O(N) path
        assert "char-elder" in nearby_ids
