#!/usr/bin/env python3
"""Recovery script: re-run post-processing passes on existing extraction output.

Applies the fixes from #243 (PC alias merge tightening) and #244 (empty
first_seen_turn + relationship dedup) to catalogs without re-extraction.

Usage:
    python tools/recover_postprocess.py --catalog-dir framework-local/catalogs
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from catalog_merger import (
    _dedup_relationships,
    cleanup_dangling_relationships,
    load_catalogs,
    load_events,
    save_catalogs,
    save_events,
)
from semantic_extraction import (
    _dedup_catalogs,
    _merge_pc_aliases,
    _rewrite_stale_ids,
)


def _fix_empty_first_seen(catalogs: dict, events_list: list) -> int:
    """Fill empty first_seen_turn fields using event data."""
    # Build entity_id → earliest turn from events
    earliest: dict[str, str] = {}
    for ev in events_list:
        turn_id = ev.get("turn_id", "")
        if not turn_id:
            continue
        for eid in ev.get("related_entities", []):
            if eid not in earliest:
                earliest[eid] = turn_id
            else:
                try:
                    cur = int(earliest[eid].split("-")[-1])
                    new = int(turn_id.split("-")[-1])
                    if new < cur:
                        earliest[eid] = turn_id
                except (ValueError, IndexError):
                    continue  # skip malformed turn IDs

    fixed = 0
    for _cat_key, entities in catalogs.items():
        for ent in entities:
            fst = ent.get("first_seen_turn", "")
            if not fst or not str(fst).strip():
                eid = ent.get("id", "")
                fallback = earliest.get(eid) or ent.get("last_updated_turn") or ""
                # Only apply if we found a reliable turn-NNN value
                if fallback and fallback.startswith("turn-"):
                    ent["first_seen_turn"] = fallback
                    fixed += 1
                    print(f"  Fixed empty first_seen_turn: {eid} → {fallback}")
                else:
                    print(f"  WARNING: no reliable turn for {eid}, skipping")
    return fixed


def main():
    parser = argparse.ArgumentParser(description="Re-run post-processing passes on existing catalogs")
    parser.add_argument("--catalog-dir", required=True, help="Path to catalogs directory")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    catalog_dir = args.catalog_dir
    if not os.path.isdir(catalog_dir):
        print(f"ERROR: {catalog_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Loading catalogs from {catalog_dir} ...")
    catalogs = load_catalogs(catalog_dir)
    events_list = load_events(catalog_dir)

    total_entities = sum(len(v) for v in catalogs.values())
    print(f"  Loaded {total_entities} entities, {len(events_list)} events")

    # --- Pass 1: Strip false-positive PC aliases FIRST ---
    # Must happen before dedup, otherwise dedup will re-merge NPCs whose
    # names appear in the PC's alias list from the broken original merge.
    print("\n=== Pass 1: Strip false-positive PC aliases ===")
    pc_entity = None
    for ent in catalogs.get("characters.json", []):
        if ent.get("id") == "char-player":
            pc_entity = ent
            break
    removed = []
    if pc_entity:
        sa = pc_entity.setdefault("stable_attributes", {})
        aliases_attr = sa.get("aliases")
        # Normalize aliases to canonical {"value": [...]} structure
        if isinstance(aliases_attr, list):
            aliases_obj = {"value": [str(a).strip() for a in aliases_attr if a]}
            sa["aliases"] = aliases_obj
        elif isinstance(aliases_attr, str):
            aliases_obj = {"value": [p.strip() for p in aliases_attr.split(",") if p.strip()]}
            sa["aliases"] = aliases_obj
        elif isinstance(aliases_attr, dict):
            aliases_obj = aliases_attr
            # Normalize value to list
            val = aliases_obj.get("value", [])
            if isinstance(val, str):
                aliases_obj["value"] = [p.strip() for p in val.split(",") if p.strip()]
        else:
            aliases_obj = {"value": []}
            sa["aliases"] = aliases_obj
        old_aliases = aliases_obj.get("value", [])
        from semantic_extraction import _PC_ALIAS_BLOCKLIST, _PC_ALIAS_WORD_BLOCKLIST

        # Build a set of NPC names that exist as independent entities.
        # If char-player has an alias matching an existing NPC name, it's
        # a false positive from the broken original merge.
        npc_names: set[str] = set()
        for ent in catalogs.get("characters.json", []):
            if ent.get("id") == "char-player":
                continue
            name = ent.get("name", "")
            if name:
                npc_names.add(name.lower().strip())
            # Also include their aliases
            for a in ent.get("stable_attributes", {}).get("aliases", {}).get("value", []):
                npc_names.add(a.lower().strip())

        clean_aliases = []
        word_bl_lower = {w.lower() for w in _PC_ALIAS_WORD_BLOCKLIST}
        for a in old_aliases:
            a_lower = a.lower().strip()
            if a_lower in _PC_ALIAS_BLOCKLIST:
                removed.append(a)
                continue
            if a_lower in word_bl_lower:
                removed.append(a)
                continue
            if a.startswith("The ") or a.startswith("the "):
                removed.append(a)
                continue
            # Strip aliases that match an existing NPC entity name
            if a_lower in npc_names:
                removed.append(a)
                continue
            clean_aliases.append(a)
        if removed:
            aliases_obj["value"] = clean_aliases
            print(f"  Stripped {len(removed)} false-positive aliases: {removed}")
            print(f"  Remaining aliases: {clean_aliases}")
        else:
            print("  No false-positive aliases to strip")

    # --- Pass 2: Fix empty first_seen_turn (#241) ---
    print("\n=== Pass 2: Fix empty first_seen_turn ===")
    fixed_fst = _fix_empty_first_seen(catalogs, events_list)
    print(f"  Fixed {fixed_fst} empty first_seen_turn fields")

    # --- Pass 3: Dedup catalogs ---
    print("\n=== Pass 3: Dedup catalogs ===")
    merge_count, merge_map = _dedup_catalogs(catalogs)
    print(f"  Merged {merge_count} duplicate entities")
    if merge_map:
        for old_id, survivor in merge_map.items():
            print(f"    {old_id} → {survivor}")
        # Rewrite stale IDs in relationships and events so dangling cleanup
        # redirects references to survivors instead of deleting them.
        _rewrite_stale_ids(catalogs, events_list, merge_map)
        print(f"  Rewrote stale IDs in relationships and events")

    # --- Pass 4: Cleanup dangling relationships + dedup relationships (#242) ---
    print("\n=== Pass 4: Cleanup dangling relationships + dedup ===")
    dangling = cleanup_dangling_relationships(catalogs)
    dangling_count = sum(len(v) for v in dangling.values())
    print(f"  Removed {dangling_count} dangling relationship references")

    # Explicit relationship dedup — cleanup_dangling doesn't call this
    dedup_count = 0
    for _cat_key, entities in catalogs.items():
        for ent in entities:
            rels = ent.get("relationships", [])
            if rels:
                deduped = _dedup_relationships(rels)
                if len(deduped) < len(rels):
                    dedup_count += len(rels) - len(deduped)
                    ent["relationships"] = deduped
    print(f"  Deduplicated {dedup_count} relationship entries")

    # --- Pass 5: PC alias merge with tightened heuristics (#239) ---
    print("\n=== Pass 5: PC alias merge (tightened) ===")
    merged_aliases = _merge_pc_aliases(
        catalogs, events_list, catalog_dir, dry_run=args.dry_run,
    )
    print(f"  Merged {len(merged_aliases)} PC alias entities")
    if merged_aliases:
        for mid in merged_aliases:
            print(f"    Merged: {mid}")

    # --- Save ---
    if not args.dry_run:
        print("\n=== Saving catalogs and events ===")
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        print("  Done.")
    else:
        print("\n=== DRY RUN — no changes written ===")

    # --- Summary ---
    print("\n=== Recovery Summary ===")
    print(f"  False-positive PC aliases stripped: {len(removed) if pc_entity else 0}")
    print(f"  Empty first_seen_turn fixed: {fixed_fst}")
    print(f"  Dedup merges: {merge_count}")
    print(f"  Dangling relationships removed: {dangling_count}")
    print(f"  PC alias entities merged: {len(merged_aliases)}")
    if pc_entity:
        final_aliases = pc_entity.get("stable_attributes", {}).get("aliases", {}).get("value", [])
        print(f"  Final PC alias count: {len(final_aliases)}")
        rels = pc_entity.get("relationships", [])
        print(f"  Final PC relationship count: {len(rels)}")


if __name__ == "__main__":
    main()
