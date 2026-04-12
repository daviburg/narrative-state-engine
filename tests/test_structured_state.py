"""Tests for structured mechanical state extraction (#86):
HP, inventory, status effects in player_state."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from extract_structured_data import (
    extract_hp,
    extract_inventory_changes,
    extract_mechanical_state,
    extract_status_effects,
    merge_mechanical_state,
    update_state_mechanical,
)


# ---------------------------------------------------------------------------
# HP extraction
# ---------------------------------------------------------------------------

class TestHPExtraction:
    def test_hp_numeric_extraction(self):
        """'HP: 15/20' -> hp.numeric: 15, hp.max_hp: 20"""
        text = "The warrior checks their status. HP: 15/20. They press on."
        hp = extract_hp(text, "turn-042")
        assert hp is not None
        assert hp["numeric"] == 15
        assert hp["max_hp"] == 20

    def test_hp_narrative_only(self):
        """'slightly wounded' -> hp.numeric: null, hp.narrative present"""
        text = "The warrior is slightly wounded from the fall."
        hp = extract_hp(text, "turn-042")
        assert hp is not None
        assert hp["numeric"] is None
        assert "slightly wounded" in hp["narrative"]

    def test_hp_damage_with_source(self):
        """'takes 5 damage from wolf bite' -> last_change with delta and source"""
        text = "The warrior takes 5 damage from the wolf bite."
        hp = extract_hp(text, "turn-042")
        assert hp is not None
        assert hp["last_change"]["delta"] == "-5"
        assert hp["last_change"]["turn"] == "turn-042"

    def test_hp_healing(self):
        """'heals 3 HP' -> last_change with positive delta"""
        text = "The cleric casts a spell and heals 3 HP."
        hp = extract_hp(text, "turn-050")
        assert hp is not None
        assert hp["last_change"]["delta"] == "+3"

    def test_hp_no_hp_info(self):
        """No HP information -> returns None"""
        text = "The party walks down the road, enjoying the sunshine."
        hp = extract_hp(text, "turn-010")
        assert hp is None

    def test_hp_restores(self):
        """'restores 8 HP' -> positive delta"""
        text = "The potion restores 8 HP instantly."
        hp = extract_hp(text, "turn-055")
        assert hp is not None
        assert hp["last_change"]["delta"] == "+8"

    def test_hp_loses(self):
        """'loses 4 HP' -> negative delta"""
        text = "The adventurer loses 4 HP to the trap."
        hp = extract_hp(text, "turn-060")
        assert hp is not None
        assert hp["last_change"]["delta"] == "-4"


# ---------------------------------------------------------------------------
# Inventory extraction
# ---------------------------------------------------------------------------

class TestInventoryExtraction:
    def test_inventory_item_with_catalog_ref(self):
        """Item in catalog -> item_id populated"""
        catalog = [{"id": "item-bone-knife", "name": "bone knife"}]
        text = "New item acquired: bone knife"
        items = extract_inventory_changes(text, items_catalog=catalog)
        assert len(items) == 1
        assert items[0]["item_id"] == "item-bone-knife"
        assert items[0]["name"] == "bone knife"
        assert items[0]["carried"] is True

    def test_inventory_item_without_catalog(self):
        """Unknown item -> item_id: None"""
        text = "New item acquired: mysterious amulet"
        items = extract_inventory_changes(text, items_catalog=[])
        assert len(items) == 1
        assert items[0]["item_id"] is None
        assert items[0]["name"] == "mysterious amulet"

    def test_inventory_narrative_acquisition(self):
        """'picks up a sword' -> inventory entry"""
        text = "The warrior picks up a rusty sword."
        items = extract_inventory_changes(text)
        assert len(items) >= 1
        assert any("sword" in item["name"].lower() for item in items)

    def test_inventory_no_items(self):
        """No item mentions -> empty list"""
        text = "The party discusses their next move around the campfire."
        items = extract_inventory_changes(text)
        assert items == []

    def test_inventory_dedup(self):
        """Same item via marker and narrative -> only one entry"""
        text = "New item acquired: silver ring\nThe hero picks up a silver ring."
        items = extract_inventory_changes(text)
        # The marker version should be picked up, narrative skipped as duplicate
        names = [i["name"].lower() for i in items]
        assert names.count("silver ring") == 1


# ---------------------------------------------------------------------------
# Status effects extraction
# ---------------------------------------------------------------------------

class TestStatusEffectsExtraction:
    def test_status_effects_extraction(self):
        """'poisoned by snake' -> status_effects entry"""
        text = "The ranger is poisoned by snake venom."
        effects = extract_status_effects(text, "turn-070")
        assert len(effects) >= 1
        poison = [e for e in effects if e["effect"] == "poisoned"]
        assert len(poison) == 1
        assert poison[0]["since_turn"] == "turn-070"

    def test_empty_status_effects(self):
        """No conditions -> empty array"""
        text = "The sun shines brightly on the meadow."
        effects = extract_status_effects(text, "turn-010")
        assert effects == []

    def test_multiple_effects(self):
        """Multiple conditions detected"""
        text = "The warrior is exhausted from the march and frightened by the dragon."
        effects = extract_status_effects(text, "turn-080")
        effect_names = {e["effect"] for e in effects}
        assert "exhausted" in effect_names
        assert "frightened" in effect_names

    def test_effect_with_source(self):
        """Source captured from 'by X' pattern"""
        text = "The mage is charmed by the enchantress."
        effects = extract_status_effects(text, "turn-090")
        assert len(effects) >= 1
        charmed = [e for e in effects if e["effect"] == "charmed"]
        assert len(charmed) == 1
        assert "source" in charmed[0]
        assert "enchantress" in charmed[0]["source"]


# ---------------------------------------------------------------------------
# Full mechanical state
# ---------------------------------------------------------------------------

class TestMechanicalState:
    def test_combined_extraction(self):
        """Extract HP, inventory, and status effects from one turn"""
        text = (
            "HP: 12/20. The warrior takes 3 damage from the goblin.\n"
            "New item acquired: goblin dagger\n"
            "The warrior is poisoned by goblin blade."
        )
        result = extract_mechanical_state(text, "turn-100")
        assert "hp" in result
        assert result["hp"]["numeric"] == 12
        assert "inventory" in result
        assert len(result["inventory"]) >= 1
        assert "status_effects" in result
        assert any(e["effect"] == "poisoned" for e in result["status_effects"])

    def test_empty_extraction(self):
        """No mechanical data -> empty dict"""
        text = "The group camps for the night under the stars."
        result = extract_mechanical_state(text, "turn-010")
        assert result == {}


# ---------------------------------------------------------------------------
# Merge mechanical state
# ---------------------------------------------------------------------------

class TestMergeMechanicalState:
    def test_merge_hp(self):
        """New HP replaces existing"""
        existing = {"location": "camp", "hp": {"narrative": "healthy", "numeric": 20, "max_hp": 20}}
        new = {"hp": {"narrative": "wounded", "numeric": 15, "max_hp": 20}}
        merged = merge_mechanical_state(existing, new, "turn-050")
        assert merged["hp"]["numeric"] == 15
        assert merged["hp"]["narrative"] == "wounded"

    def test_merge_inventory_add(self):
        """New items added to existing inventory"""
        existing = {"inventory": [{"item_id": None, "name": "rope", "carried": True, "quantity": 1, "notes": None}]}
        new = {"inventory": [{"item_id": None, "name": "torch", "carried": True, "quantity": 1, "notes": None}]}
        merged = merge_mechanical_state(existing, new, "turn-050")
        assert len(merged["inventory"]) == 2

    def test_merge_status_add(self):
        """New effects added, existing preserved"""
        existing = {"status_effects": [{"effect": "fatigued", "since_turn": "turn-040"}]}
        new = {"status_effects": [{"effect": "poisoned", "source": "snake", "since_turn": "turn-050"}]}
        merged = merge_mechanical_state(existing, new, "turn-050")
        assert len(merged["status_effects"]) == 2


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_backward_compatible(self):
        """Old state.json without new fields still works — existing fields preserved"""
        existing = {
            "location": "camp",
            "condition": "healthy",
            "inventory_notes": "carrying basic supplies",
        }
        # No mechanical data extracted
        merged = merge_mechanical_state(existing, {}, "turn-010")
        assert merged["location"] == "camp"
        assert merged["condition"] == "healthy"
        assert merged["inventory_notes"] == "carrying basic supplies"
        assert "hp" not in merged
        assert "inventory" not in merged
        assert "status_effects" not in merged


# ---------------------------------------------------------------------------
# update_state_mechanical (file I/O)
# ---------------------------------------------------------------------------

class TestUpdateStateMechanical:
    def test_updates_state_file(self, tmp_path):
        """Mechanical state written to state.json"""
        derived_dir = str(tmp_path / "derived")
        os.makedirs(derived_dir)
        state = {
            "as_of_turn": "turn-042",
            "current_world_state": "test",
            "player_state": {"location": "camp", "condition": "healthy"},
            "active_threads": [],
        }
        with open(os.path.join(derived_dir, "state.json"), "w") as f:
            json.dump(state, f)

        text = "HP: 15/20. The hero is poisoned by the snake."
        result = update_state_mechanical(derived_dir, text, "turn-042")

        assert "hp" in result

        with open(os.path.join(derived_dir, "state.json")) as f:
            updated = json.load(f)
        assert updated["player_state"]["hp"]["numeric"] == 15
        assert any(
            e["effect"] == "poisoned"
            for e in updated["player_state"]["status_effects"]
        )

    def test_no_state_file(self, tmp_path):
        """Returns extraction result even if state.json missing"""
        derived_dir = str(tmp_path / "derived")
        os.makedirs(derived_dir)
        text = "HP: 10/20"
        result = update_state_mechanical(derived_dir, text, "turn-001")
        assert "hp" in result

    def test_dry_run(self, tmp_path):
        """Dry run doesn't modify state.json"""
        derived_dir = str(tmp_path / "derived")
        os.makedirs(derived_dir)
        state = {
            "as_of_turn": "turn-042",
            "current_world_state": "test",
            "player_state": {},
            "active_threads": [],
        }
        state_file = os.path.join(derived_dir, "state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        text = "HP: 15/20"
        update_state_mechanical(derived_dir, text, "turn-042", dry_run=True)

        with open(state_file) as f:
            unchanged = json.load(f)
        assert "hp" not in unchanged["player_state"]
