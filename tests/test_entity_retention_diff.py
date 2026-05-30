"""Tests for entity_retention_diff.py — per-entity retention diff between runs."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from entity_retention_diff import (
    compute_retention_diff,
    diff_id_sets,
    format_markdown,
    main,
    match_entities,
)

_TYPE_DIRS = {
    "char-": "characters",
    "loc-": "locations",
    "faction-": "factions",
    "item-": "items",
    "creature-": "characters",
    "concept-": "items",
}


def _write_catalog(base_dir, entity_ids, event_ids=None):
    """Create a V2 catalogs directory populated with the given entity IDs.

    entity_ids: iterable of entity IDs (prefix determines the type directory).
    event_ids: optional iterable of event IDs written to events.json.
    """
    catalog_dir = os.path.join(base_dir, "catalogs")
    for entity_id in entity_ids:
        prefix = next((p for p in _TYPE_DIRS if entity_id.startswith(p)), "char-")
        dirname = _TYPE_DIRS[prefix]
        type_dir = os.path.join(catalog_dir, dirname)
        os.makedirs(type_dir, exist_ok=True)
        entity = {
            "id": entity_id,
            "name": entity_id,
            "type": dirname[:-1] if dirname.endswith("s") else dirname,
            "identity": f"{entity_id} identity",
            "first_seen_turn": "turn-001",
        }
        with open(os.path.join(type_dir, f"{entity_id}.json"), "w", encoding="utf-8") as f:
            json.dump(entity, f)

    if event_ids is not None:
        os.makedirs(catalog_dir, exist_ok=True)
        events = [{"id": eid, "summary": eid} for eid in event_ids]
        with open(os.path.join(catalog_dir, "events.json"), "w", encoding="utf-8") as f:
            json.dump(events, f)

    return catalog_dir


def _write_named_catalog(base_dir, entities):
    """Create a V2 catalogs dir from explicit entity specs.

    entities: iterable of dicts with keys ``id`` and ``name`` and an optional
    ``aliases`` list (stored under ``stable_attributes.aliases.value``).
    The ID prefix determines the type directory.
    """
    catalog_dir = os.path.join(base_dir, "catalogs")
    for spec in entities:
        entity_id = spec["id"]
        prefix = next((p for p in _TYPE_DIRS if entity_id.startswith(p)), "char-")
        dirname = _TYPE_DIRS[prefix]
        type_dir = os.path.join(catalog_dir, dirname)
        os.makedirs(type_dir, exist_ok=True)
        entity = {
            "id": entity_id,
            "name": spec["name"],
            "type": dirname[:-1] if dirname.endswith("s") else dirname,
            "identity": f"{entity_id} identity",
            "first_seen_turn": "turn-001",
        }
        aliases = spec.get("aliases")
        if aliases:
            entity["stable_attributes"] = {"aliases": {"value": list(aliases)}}
        with open(os.path.join(type_dir, f"{entity_id}.json"), "w", encoding="utf-8") as f:
            json.dump(entity, f)
    return catalog_dir


class TestDiffIdSets:
    def test_retained_removed_added(self):
        result = diff_id_sets({"a", "b", "c"}, {"b", "c", "d"})
        assert result["retained"] == ["b", "c"]
        assert result["removed"] == ["a"]
        assert result["added"] == ["d"]

    def test_identical_sets(self):
        result = diff_id_sets({"a", "b"}, {"a", "b"})
        assert result["retained"] == ["a", "b"]
        assert result["removed"] == []
        assert result["added"] == []

    def test_empty_sets(self):
        result = diff_id_sets(set(), set())
        assert result == {"retained": [], "removed": [], "added": []}

    def test_results_are_sorted(self):
        result = diff_id_sets({"c", "a", "b"}, set())
        assert result["removed"] == ["a", "b", "c"]


class TestComputeRetentionDiff:
    def test_detects_retained_removed_added(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob", "loc-keep"])
        # bob removed, carol added, keep retained
        _write_catalog(str(dir_b), ["char-alice", "char-carol", "loc-keep"])

        report = compute_retention_diff(str(dir_a), str(dir_b))

        chars = report["by_type"]["characters"]
        assert chars["retained"] == ["char-alice"]
        assert chars["removed"] == ["char-bob"]
        assert chars["added"] == ["char-carol"]

        locs = report["by_type"]["locations"]
        assert locs["retained"] == ["loc-keep"]
        assert locs["removed"] == []
        assert locs["added"] == []

    def test_totals(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice", "char-carol", "item-sword"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        totals = report["totals"]
        assert totals["a"] == 2
        assert totals["b"] == 3
        assert totals["retained"] == 1
        assert totals["removed"] == 1
        assert totals["added"] == 2
        assert totals["net_change"] == 1

    def test_flag_on_removal_default_threshold(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["flagged"] is True
        assert report["flagged_types"] == ["characters"]
        assert report["removal_threshold"] == 0

    def test_threshold_suppresses_flag(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(str(dir_a), str(dir_b), removal_threshold=1)
        # one removal, threshold of 1 -> not flagged (removed > threshold is False)
        assert report["flagged"] is False
        # flagged_types is empty when the flag is not raised
        assert report["flagged_types"] == []

    def test_no_flag_when_only_added(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["char-alice", "char-bob"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["flagged"] is False
        assert report["flagged_types"] == []

    def test_empty_catalogs(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        os.makedirs(dir_a / "catalogs", exist_ok=True)
        os.makedirs(dir_b / "catalogs", exist_ok=True)

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["totals"]["a"] == 0
        assert report["totals"]["b"] == 0
        assert report["flagged"] is False
        for entity_type in ("characters", "locations", "items", "factions", "events"):
            assert report["by_type"][entity_type]["removed"] == []

    def test_missing_type_files(self, tmp_path):
        # A has characters only; B has locations only (no characters dir).
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["loc-keep"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["by_type"]["characters"]["removed"] == ["char-alice"]
        assert report["by_type"]["locations"]["added"] == ["loc-keep"]

    def test_events_diff(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), [], event_ids=["evt-001", "evt-002"])
        _write_catalog(str(dir_b), [], event_ids=["evt-001", "evt-003"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        events = report["by_type"]["events"]
        assert events["retained"] == ["evt-001"]
        assert events["removed"] == ["evt-002"]
        assert events["added"] == ["evt-003"]

    def test_accepts_catalogs_dir_directly(self, tmp_path):
        # Pass the catalogs/ directory itself rather than the framework dir.
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        catalogs_a = _write_catalog(str(dir_a), ["char-alice"])
        catalogs_b = _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(catalogs_a, catalogs_b)
        assert report["totals"]["retained"] == 1

    def test_negative_threshold_raises(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["char-alice"])

        import pytest
        with pytest.raises(ValueError, match="removal_threshold must be >= 0"):
            compute_retention_diff(str(dir_a), str(dir_b), removal_threshold=-1)

    def test_invalid_layout_raises(self, tmp_path):
        # A directory that exists but has no catalogs/ subdir and is not named catalogs.
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        import pytest
        with pytest.raises(ValueError, match="Cannot resolve catalog directory"):
            compute_retention_diff(str(dir_a), str(dir_b))


class TestFormatMarkdown:
    def test_flagged_output_lists_removed_ids(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        md = format_markdown(report)
        assert "FLAGGED" in md
        assert "char-bob" in md
        assert "| **Total** |" in md

    def test_clean_output(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        md = format_markdown(report)
        assert "No retention regression" in md

    def test_within_threshold_lists_removed_ids(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        report = compute_retention_diff(str(dir_a), str(dir_b), removal_threshold=5)
        md = format_markdown(report)
        assert "No retention regression" in md
        assert "char-bob" in md


class TestMainCli:
    def test_strict_flagged_exits_1(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--strict"])
        assert rc == 1

    def test_strict_clean_exits_0(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["char-alice"])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--strict"])
        assert rc == 0

    def test_flagged_without_strict_exits_0(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice", "char-bob"])
        _write_catalog(str(dir_b), ["char-alice"])

        rc = main(["-a", str(dir_a), "-b", str(dir_b)])
        assert rc == 0

    def test_missing_dir_exits_2(self, tmp_path):
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_b), ["char-alice"])

        rc = main(["-a", str(tmp_path / "nope"), "-b", str(dir_b)])
        assert rc == 2

    def test_json_output(self, tmp_path, capsys):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_catalog(str(dir_a), ["char-alice"])
        _write_catalog(str(dir_b), ["char-alice"])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"]["retained"] == 1


class TestMatchEntities:
    def test_id_mode_exact_only(self):
        a = [{"id": "char-elder", "name_keys": {"elder"}}]
        b = [{"id": "char-elder-001", "name_keys": {"elder"}}]
        result = match_entities(a, b, match_by="id")
        assert result["removed"] == ["char-elder"]
        assert result["added"] == ["char-elder-001"]
        assert result["renamed"] == []

    def test_auto_name_rename(self):
        a = [{"id": "char-elder", "name_keys": {"elder"}}]
        b = [{"id": "char-elder-001", "name_keys": {"elder"}}]
        result = match_entities(a, b, match_by="auto")
        assert result["removed"] == []
        assert result["added"] == []
        assert result["renamed"] == [
            {"old_id": "char-elder", "new_id": "char-elder-001"}
        ]

    def test_auto_exact_id_retained(self):
        a = [{"id": "char-elder", "name_keys": {"elder"}}]
        b = [{"id": "char-elder", "name_keys": {"elder"}}]
        result = match_entities(a, b, match_by="auto")
        assert result["retained"] == ["char-elder"]
        assert result["renamed"] == []

    def test_name_mode_same_id_retained(self):
        a = [{"id": "char-elder", "name_keys": {"elder"}}]
        b = [{"id": "char-elder", "name_keys": {"elder"}}]
        result = match_entities(a, b, match_by="name")
        assert result["retained"] == ["char-elder"]
        assert result["removed"] == []
        assert result["renamed"] == []

    def test_no_name_keys_only_id_matches(self):
        # Entities without name keys (e.g. events) can only match by ID.
        a = [{"id": "evt-001", "name_keys": set()}, {"id": "evt-002", "name_keys": set()}]
        b = [{"id": "evt-001", "name_keys": set()}, {"id": "evt-003", "name_keys": set()}]
        result = match_entities(a, b, match_by="auto")
        assert result["retained"] == ["evt-001"]
        assert result["removed"] == ["evt-002"]
        assert result["added"] == ["evt-003"]
        assert result["renamed"] == []


class TestMatchByAuto:
    def test_identical_ids_zero_churn(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder", "name": "Elder"}])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["totals"]["removed"] == 0
        assert report["totals"]["added"] == 0
        assert report["totals"]["renamed"] == 0
        assert report["totals"]["retained"] == 1
        assert report["flagged"] is False

    def test_suffix_rename_is_not_churn(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        # main's bare slug vs compression branch's numeric-suffixed ID, same name.
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["totals"]["removed"] == 0
        assert report["totals"]["added"] == 0
        assert report["totals"]["renamed"] == 1
        assert report["by_type"]["characters"]["renamed"] == [
            {"old_id": "char-elder", "new_id": "char-elder-001"}
        ]
        assert report["flagged"] is False

    def test_suffix_rename_is_churn_under_match_by_id(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        report = compute_retention_diff(str(dir_a), str(dir_b), match_by="id")
        assert report["by_type"]["characters"]["removed"] == ["char-elder"]
        assert report["by_type"]["characters"]["added"] == ["char-elder-001"]
        assert report["totals"]["renamed"] == 0
        assert report["flagged"] is True

    def test_genuine_removal_is_true_removed(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(
            str(dir_a),
            [{"id": "char-elder", "name": "Elder"}, {"id": "char-mara", "name": "Mara"}],
        )
        _write_named_catalog(str(dir_b), [{"id": "char-elder", "name": "Elder"}])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["by_type"]["characters"]["removed"] == ["char-mara"]
        assert report["totals"]["renamed"] == 0
        assert report["flagged"] is True

    def test_genuine_addition_is_true_added(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(
            str(dir_b),
            [{"id": "char-elder", "name": "Elder"}, {"id": "char-new", "name": "Newbie"}],
        )

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["by_type"]["characters"]["added"] == ["char-new"]
        assert report["totals"]["removed"] == 0
        assert report["totals"]["renamed"] == 0
        assert report["flagged"] is False

    def test_alias_match_is_rename_not_removal(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        # B's name differs, but "Elder" appears as an alias -> matched.
        _write_named_catalog(
            str(dir_b),
            [{"id": "char-sage", "name": "The Sage", "aliases": ["Elder"]}],
        )

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["totals"]["removed"] == 0
        assert report["totals"]["added"] == 0
        assert report["totals"]["renamed"] == 1
        assert report["by_type"]["characters"]["renamed"] == [
            {"old_id": "char-elder", "new_id": "char-sage"}
        ]

    def test_same_name_different_type_not_matched(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        # Same display name "Relic" but one is a character, one an item.
        _write_named_catalog(str(dir_a), [{"id": "char-relic", "name": "Relic"}])
        _write_named_catalog(str(dir_b), [{"id": "item-relic", "name": "Relic"}])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        assert report["by_type"]["characters"]["removed"] == ["char-relic"]
        assert report["by_type"]["items"]["added"] == ["item-relic"]
        assert report["totals"]["renamed"] == 0
        assert report["totals"]["removed"] == 1
        assert report["totals"]["added"] == 1

    def test_strict_rename_only_exits_zero(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--strict"])
        assert rc == 0

    def test_strict_true_removal_exits_one(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(
            str(dir_a),
            [{"id": "char-elder", "name": "Elder"}, {"id": "char-mara", "name": "Mara"}],
        )
        _write_named_catalog(str(dir_b), [{"id": "char-elder", "name": "Elder"}])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--strict"])
        assert rc == 1

    def test_invalid_match_by_raises(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder", "name": "Elder"}])

        import pytest
        with pytest.raises(ValueError, match="match_by must be one of"):
            compute_retention_diff(str(dir_a), str(dir_b), match_by="bogus")

    def test_json_includes_renamed(self, tmp_path, capsys):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["match_by"] == "auto"
        assert payload["totals"]["renamed"] == 1
        assert payload["by_type"]["characters"]["renamed"] == [
            {"old_id": "char-elder", "new_id": "char-elder-001"}
        ]

    def test_cli_match_by_id_reports_churn(self, tmp_path, capsys):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        rc = main(["-a", str(dir_a), "-b", str(dir_b), "--match-by", "id", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["match_by"] == "id"
        assert payload["totals"]["removed"] == 1
        assert payload["totals"]["added"] == 1
        assert payload["totals"]["renamed"] == 0

    def test_markdown_shows_rename_section(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_named_catalog(str(dir_a), [{"id": "char-elder", "name": "Elder"}])
        _write_named_catalog(str(dir_b), [{"id": "char-elder-001", "name": "Elder"}])

        report = compute_retention_diff(str(dir_a), str(dir_b))
        md = format_markdown(report)
        assert "ID renames" in md
        assert "char-elder -> char-elder-001" in md
        assert "No retention regression" in md


