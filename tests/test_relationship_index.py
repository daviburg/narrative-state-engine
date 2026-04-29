"""Tests for the reverse relationship index (generate_relationship_index,
save_relationship_index)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (
    generate_relationship_index,
    save_relationship_index,
    save_catalogs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(id_, name, etype="character", turn="turn-001", relationships=None):
    """Create a minimal V2 entity dict."""
    return {
        "id": id_,
        "name": name,
        "type": etype,
        "identity": f"{name} identity.",
        "first_seen_turn": turn,
        "last_updated_turn": turn,
        "relationships": relationships or [],
    }


def _make_rel(target_id, rel_text, rel_type="social", **kwargs):
    """Create a minimal relationship dict."""
    r = {
        "target_id": target_id,
        "current_relationship": rel_text,
        "type": rel_type,
        "status": "active",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
    }
    r.update(kwargs)
    return r


# ---------------------------------------------------------------------------
# generate_relationship_index
# ---------------------------------------------------------------------------

class TestGenerateRelationshipIndex:
    def test_empty_catalogs(self):
        catalogs = {"characters.json": [], "locations.json": [], "factions.json": [], "items.json": []}
        entries = generate_relationship_index(catalogs)
        assert entries == {}

    def test_entities_with_no_relationships(self):
        catalogs = {
            "characters.json": [_make_entity("char-a", "Alice")],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        assert entries == {}

    def test_forward_relationship(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
                _make_entity("char-b", "Bob"),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        # Alice should have one forward edge
        assert "char-a" in entries
        assert len(entries["char-a"]["forward"]) == 1
        assert len(entries["char-a"]["reverse"]) == 0
        assert entries["char-a"]["forward"][0]["target_id"] == "char-b"
        assert entries["char-a"]["forward"][0]["source_name"] == "Alice"

        # Bob should have one reverse edge
        assert "char-b" in entries
        assert len(entries["char-b"]["forward"]) == 0
        assert len(entries["char-b"]["reverse"]) == 1
        assert entries["char-b"]["reverse"][0]["source_id"] == "char-a"
        assert entries["char-b"]["reverse"][0]["target_name"] == "Bob"

    def test_bidirectional_relationships(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
                _make_entity("char-b", "Bob", relationships=[
                    _make_rel("char-a", "friends with"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        # Both should have 1 forward and 1 reverse
        assert len(entries["char-a"]["forward"]) == 1
        assert len(entries["char-a"]["reverse"]) == 1
        assert len(entries["char-b"]["forward"]) == 1
        assert len(entries["char-b"]["reverse"]) == 1

    def test_cross_type_relationships(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("loc-tavern", "resides at"),
                    _make_rel("faction-guild", "member of", rel_type="factional"),
                ]),
            ],
            "locations.json": [_make_entity("loc-tavern", "Tavern", etype="location")],
            "factions.json": [_make_entity("faction-guild", "Thieves Guild", etype="faction")],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        # Alice: 2 forward, 0 reverse
        assert len(entries["char-a"]["forward"]) == 2
        assert len(entries["char-a"]["reverse"]) == 0

        # Tavern: 0 forward, 1 reverse
        assert len(entries["loc-tavern"]["forward"]) == 0
        assert len(entries["loc-tavern"]["reverse"]) == 1
        assert entries["loc-tavern"]["entity_type"] == "location"

        # Guild: 0 forward, 1 reverse
        assert len(entries["faction-guild"]["forward"]) == 0
        assert len(entries["faction-guild"]["reverse"]) == 1

    def test_target_not_in_catalogs(self):
        """Relationships to entities not in catalogs are still indexed."""
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-unknown", "heard rumors about"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        assert "char-a" in entries
        assert "char-unknown" in entries
        # Unknown entity gets empty name but type inferred from ID prefix
        assert entries["char-unknown"]["entity_name"] == ""
        assert entries["char-unknown"]["entity_type"] == "character"
        assert len(entries["char-unknown"]["reverse"]) == 1

    def test_dangling_target_type_inferred_from_prefix(self):
        """Entity type is inferred from ID prefix for all known prefixes."""
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("loc-somewhere", "traveled to"),
                    _make_rel("faction-secret", "investigated"),
                    _make_rel("item-amulet", "carries"),
                    _make_rel("creature-wolf", "fought"),
                    _make_rel("concept-honor", "values"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        assert entries["loc-somewhere"]["entity_type"] == "location"
        assert entries["faction-secret"]["entity_type"] == "faction"
        assert entries["item-amulet"]["entity_type"] == "item"
        assert entries["creature-wolf"]["entity_type"] == "creature"
        assert entries["concept-honor"]["entity_type"] == "concept"

    def test_dangling_target_unknown_prefix_empty_type(self):
        """Unrecognized ID prefix results in empty entity_type."""
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("mystery-thing", "encountered"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        assert entries["mystery-thing"]["entity_type"] == ""

    def test_optional_fields_preserved(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "wary of", direction="outgoing",
                              first_seen_turn="turn-004", last_updated_turn="turn-006"),
                ]),
                _make_entity("char-b", "Bob"),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        edge = entries["char-a"]["forward"][0]
        assert edge["direction"] == "outgoing"
        assert edge["first_seen_turn"] == "turn-004"
        assert edge["last_updated_turn"] == "turn-006"

    def test_dormant_relationships_included(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "formerly allied with", status="dormant"),
                ]),
                _make_entity("char-b", "Bob"),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        assert entries["char-a"]["forward"][0]["status"] == "dormant"
        assert entries["char-b"]["reverse"][0]["status"] == "dormant"

    def test_multiple_relationships_same_source(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "friends with"),
                    _make_rel("char-c", "rivals with", rel_type="adversarial"),
                    _make_rel("loc-tavern", "works at"),
                ]),
                _make_entity("char-b", "Bob"),
                _make_entity("char-c", "Carol"),
            ],
            "locations.json": [_make_entity("loc-tavern", "Tavern", etype="location")],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)
        assert len(entries["char-a"]["forward"]) == 3
        assert len(entries["char-b"]["reverse"]) == 1
        assert len(entries["char-c"]["reverse"]) == 1
        assert len(entries["loc-tavern"]["reverse"]) == 1

    def test_spatial_relationships_indexed(self):
        """Spatial relationship type is indexed correctly for reverse lookups."""
        catalogs = {
            "characters.json": [
                _make_entity("char-elder", "The Elder", relationships=[
                    _make_rel("loc-village-square", "resides_at", rel_type="spatial",
                              direction="outgoing"),
                ]),
                _make_entity("char-guard", "Guard", relationships=[
                    _make_rel("loc-village-square", "stationed_at", rel_type="spatial",
                              direction="outgoing"),
                ]),
            ],
            "locations.json": [
                _make_entity("loc-village-square", "Village Square", etype="location"),
            ],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        # Village Square should have 2 reverse spatial relationships
        loc_entry = entries["loc-village-square"]
        assert len(loc_entry["reverse"]) == 2
        assert all(r["type"] == "spatial" for r in loc_entry["reverse"])
        source_ids = {r["source_id"] for r in loc_entry["reverse"]}
        assert source_ids == {"char-elder", "char-guard"}

        # Elder and Guard each have 1 forward spatial relationship
        assert len(entries["char-elder"]["forward"]) == 1
        assert entries["char-elder"]["forward"][0]["type"] == "spatial"
        assert len(entries["char-guard"]["forward"]) == 1

    def test_spatial_location_to_location_indexed(self):
        """Spatial connections between locations appear in both entries."""
        catalogs = {
            "characters.json": [],
            "locations.json": [
                _make_entity("loc-tavern", "Tavern", etype="location", relationships=[
                    _make_rel("loc-market", "connected_to", rel_type="spatial",
                              direction="bidirectional"),
                ]),
                _make_entity("loc-market", "Market Square", etype="location"),
            ],
            "factions.json": [],
            "items.json": [],
        }
        entries = generate_relationship_index(catalogs)

        # Tavern has 1 forward, Market has 1 reverse
        assert len(entries["loc-tavern"]["forward"]) == 1
        assert entries["loc-tavern"]["forward"][0]["type"] == "spatial"
        assert len(entries["loc-market"]["reverse"]) == 1
        assert entries["loc-market"]["reverse"][0]["source_id"] == "loc-tavern"


# ---------------------------------------------------------------------------
# save_relationship_index
# ---------------------------------------------------------------------------

class TestSaveRelationshipIndex:
    def test_writes_file(self, tmp_path):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", turn="turn-005", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
                _make_entity("char-b", "Bob", turn="turn-003"),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_relationship_index(str(tmp_path), catalogs, turn_id="turn-005")
        fpath = tmp_path / "relationship-index.json"
        assert fpath.exists()
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert data["generated_turn"] == "turn-005"
        assert "char-a" in data["entries"]
        assert "char-b" in data["entries"]

    def test_auto_detects_turn(self, tmp_path):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", turn="turn-010", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
                _make_entity("char-b", "Bob", turn="turn-007"),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_relationship_index(str(tmp_path), catalogs)
        data = json.loads((tmp_path / "relationship-index.json").read_text(encoding="utf-8"))
        assert data["generated_turn"] == "turn-010"

    def test_dry_run_does_not_write(self, tmp_path):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_relationship_index(str(tmp_path), catalogs, dry_run=True)
        assert not (tmp_path / "relationship-index.json").exists()

    def test_empty_catalogs_writes_empty_entries(self, tmp_path):
        catalogs = {"characters.json": [], "locations.json": [], "factions.json": [], "items.json": []}
        save_relationship_index(str(tmp_path), catalogs)
        data = json.loads((tmp_path / "relationship-index.json").read_text(encoding="utf-8"))
        assert data["entries"] == {}
        assert data["generated_turn"] == "turn-000"


# ---------------------------------------------------------------------------
# Integration: save_catalogs triggers relationship index
# ---------------------------------------------------------------------------

class TestSaveCatalogsRelationshipIndex:
    def test_save_catalogs_generates_relationship_index(self, tmp_path):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", turn="turn-005", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
                _make_entity("char-b", "Bob", turn="turn-003"),
            ],
            "locations.json": [_make_entity("loc-tavern", "Tavern", etype="location")],
            "factions.json": [],
            "items.json": [],
        }
        save_catalogs(str(tmp_path), catalogs)

        # Relationship index should be generated alongside per-type indexes
        rel_index_path = tmp_path / "relationship-index.json"
        assert rel_index_path.exists()
        data = json.loads(rel_index_path.read_text(encoding="utf-8"))
        assert "char-a" in data["entries"]
        assert "char-b" in data["entries"]
        # loc-tavern has no relationships, should not appear
        assert "loc-tavern" not in data["entries"]

    def test_save_catalogs_dry_run_skips_relationship_index(self, tmp_path):
        catalogs = {
            "characters.json": [
                _make_entity("char-a", "Alice", relationships=[
                    _make_rel("char-b", "friends with"),
                ]),
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_catalogs(str(tmp_path), catalogs, dry_run=True)
        assert not (tmp_path / "relationship-index.json").exists()
