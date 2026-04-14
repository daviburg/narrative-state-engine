#!/usr/bin/env python3
"""
generate_wiki_pages.py — Generate human-readable wiki-style markdown pages
from per-entity JSON catalog files.

Reads V2 per-entity JSON files and produces:
- Individual .md pages alongside each entity JSON file
- Index README.md pages per entity type directory

Usage:
    python tools/generate_wiki_pages.py --framework framework-local/
    python tools/generate_wiki_pages.py --framework framework/ --type characters
    python tools/generate_wiki_pages.py --framework framework-local/ --index-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Entity type directories
ENTITY_TYPES = ["characters", "locations", "factions", "items"]

# Display labels for entity types
TYPE_LABELS = {
    "characters": "Character",
    "locations": "Location",
    "factions": "Faction",
    "items": "Item",
}


def _load_entity(filepath: str) -> dict | None:
    """Load a single entity JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_all_entities(catalog_dir: str) -> dict[str, list[dict]]:
    """Load all entities from all type directories."""
    all_entities: dict[str, list[dict]] = {}
    for entity_type in ENTITY_TYPES:
        type_dir = os.path.join(catalog_dir, entity_type)
        entities = []
        if os.path.isdir(type_dir):
            for fname in sorted(os.listdir(type_dir)):
                if fname == "index.json" or not fname.endswith(".json"):
                    continue
                entity = _load_entity(os.path.join(type_dir, fname))
                if entity:
                    entities.append(entity)
        all_entities[entity_type] = entities
    return all_entities


def _build_name_index(all_entities: dict[str, list[dict]]) -> dict[str, tuple[str, str]]:
    """Build a mapping from entity ID to (name, relative_md_path).

    The path is relative from any entity-type directory (e.g. ../characters/char-player.md).
    """
    index: dict[str, tuple[str, str]] = {}
    for entity_type, entities in all_entities.items():
        for entity in entities:
            eid = entity.get("id", "")
            name = entity.get("name", eid)
            md_path = f"../{entity_type}/{eid}.md"
            index[eid] = (name, md_path)
    return index


def _resolve_target(target_id: str, name_index: dict[str, tuple[str, str]],
                    current_type: str) -> str:
    """Resolve a target_id to a markdown link or raw ID."""
    if target_id in name_index:
        name, md_path = name_index[target_id]
        # If same directory, use simple relative path
        target_type = _infer_type_from_id(target_id)
        if target_type == current_type:
            return f"[{name}]({target_id}.md)"
        return f"[{name}]({md_path})"
    return target_id


def _infer_type_from_id(entity_id: str) -> str:
    """Infer entity type directory from ID prefix."""
    prefix_map = {
        "char-": "characters",
        "loc-": "locations",
        "faction-": "factions",
        "item-": "items",
        "creature-": "characters",
        "concept-": "items",
    }
    for prefix, etype in prefix_map.items():
        if entity_id.startswith(prefix):
            return etype
    return ""


def _format_attr_value(value) -> str:
    """Format an attribute value for display in a markdown table cell."""
    if isinstance(value, list):
        raw = ", ".join(str(v) for v in value)
    else:
        raw = str(value)
    return _escape_table_cell(raw)


def _escape_table_cell(text: str) -> str:
    """Escape text for safe inclusion in a markdown table cell."""
    text = str(text)
    text = text.replace("|", "\\|")
    text = text.replace("\n", " ")
    text = text.replace("\r", "")
    return text


def _parse_turn_number(turn_id: str) -> int:
    """Extract numeric part from turn ID for sorting."""
    m = re.match(r"^turn-(\d+)$", turn_id or "")
    return int(m.group(1)) if m else 0


def _type_label(entity: dict, fallback: str) -> str:
    """Return a display label for the entity type.

    Uses the entity's own ``type`` field so that creature-* and concept-*
    entries get the correct label instead of always showing the catalog
    directory name.
    """
    raw = entity.get("type", fallback).lower()
    return raw.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Character page
# ---------------------------------------------------------------------------

def generate_character_page(entity: dict, name_index: dict[str, tuple[str, str]]) -> str:
    """Generate a wiki-style markdown page for a character entity."""
    eid = entity.get("id", "")
    name = entity.get("name", eid)
    identity = entity.get("identity", "")
    first_seen = entity.get("first_seen_turn", "")
    last_updated = entity.get("last_updated_turn", "")
    current_status = entity.get("current_status", "")
    status_turn = entity.get("status_updated_turn", last_updated)
    stable_attrs = entity.get("stable_attributes", {})
    volatile = entity.get("volatile_state", {})
    relationships = entity.get("relationships", [])

    lines = []
    lines.append(f"# {name}\n")
    if identity:
        lines.append(f"> {identity}\n")

    # Infobox
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Type** | {_type_label(entity, 'Character')} |")
    lines.append(f"| **First Seen** | {first_seen} |")
    lines.append(f"| **Last Updated** | {last_updated} |")
    # Add simple stable attributes to infobox
    for key, attr in stable_attrs.items():
        if key == "aliases":
            continue  # shown separately
        if isinstance(attr, dict):
            val = attr.get("value", "")
        else:
            val = attr
        display_val = _format_attr_value(val)
        if len(display_val) <= 80:
            lines.append(f"| **{key.replace('_', ' ').title()}** | {display_val} |")
    lines.append("")

    # Current Status
    if current_status:
        lines.append("## Current Status\n")
        lines.append(f"*As of {status_turn}:*\n")
        lines.append(f"{current_status}\n")

    # Attributes section
    if stable_attrs:
        lines.append("## Attributes\n")
        lines.append("### Stable Traits\n")
        lines.append("| Trait | Value | Source | Confidence |")
        lines.append("|---|---|---|---|")
        for key, attr in stable_attrs.items():
            if isinstance(attr, dict):
                val = _format_attr_value(attr.get("value", ""))
                source = attr.get("source_turn", "")
                confidence = attr.get("confidence", "")
                inference = attr.get("inference", False)
                conf_str = f"{confidence}"
                if inference:
                    conf_str += " (inferred)"
            else:
                val = _format_attr_value(attr)
                source = ""
                conf_str = ""
            lines.append(f"| {key} | {val} | {source} | {conf_str} |")
        lines.append("")

    # Current State
    if volatile:
        lines.append("### Current State\n")
        condition = volatile.get("condition", "")
        equipment = volatile.get("equipment", [])
        location = volatile.get("location", "")
        if condition:
            lines.append(f"- **Condition:** {condition}")
        if equipment:
            equip_str = ", ".join(equipment) if isinstance(equipment, list) else str(equipment)
            lines.append(f"- **Equipment:** {equip_str}")
        if location:
            lines.append(f"- **Location:** {location}")
        lines.append("")

    # Relationships
    if relationships:
        lines.append("## Relationships\n")
        lines.append("| Entity | Relationship | Type | Status |")
        lines.append("|---|---|---|---|")
        for rel in relationships:
            target_id = rel.get("target_id", "")
            target_display = _resolve_target(target_id, name_index, "characters")
            cur_rel = _escape_table_cell(rel.get("current_relationship", ""))
            rel_type = _escape_table_cell(rel.get("type", ""))
            status = _escape_table_cell(rel.get("status", ""))
            lines.append(f"| {target_display} | {cur_rel} | {rel_type} | {status} |")
        lines.append("")

        # Relationship history
        rels_with_history = [r for r in relationships if r.get("history")]
        if rels_with_history:
            lines.append("### Relationship History\n")
            for rel in rels_with_history:
                target_id = rel.get("target_id", "")
                target_display = _resolve_target(target_id, name_index, "characters")
                rel_type = rel.get("type", "")
                lines.append(f"#### → {target_display} ({rel_type})\n")
                for entry in rel["history"]:
                    turn = entry.get("turn", "")
                    desc = entry.get("description", "")
                    lines.append(f"- **{turn}:** {desc}")
                lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated from [{eid}.json]({eid}.json) — do not edit manually.*")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Location page
# ---------------------------------------------------------------------------

def generate_location_page(entity: dict, name_index: dict[str, tuple[str, str]]) -> str:
    """Generate a wiki-style markdown page for a location entity."""
    eid = entity.get("id", "")
    name = entity.get("name", eid)
    identity = entity.get("identity", "")
    first_seen = entity.get("first_seen_turn", "")
    last_updated = entity.get("last_updated_turn", "")
    current_status = entity.get("current_status", "")
    stable_attrs = entity.get("stable_attributes", {})
    relationships = entity.get("relationships", [])

    lines = []
    lines.append(f"# {name}\n")
    if identity:
        lines.append(f"> {identity}\n")

    # Infobox
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Type** | {_type_label(entity, 'Location')} |")
    lines.append(f"| **First Seen** | {first_seen} |")
    lines.append(f"| **Last Updated** | {last_updated} |")
    for key, attr in stable_attrs.items():
        if key == "aliases":
            continue
        if isinstance(attr, dict):
            val = attr.get("value", "")
        else:
            val = attr
        display_val = _format_attr_value(val)
        if len(display_val) <= 80:
            lines.append(f"| **{key.replace('_', ' ').title()}** | {display_val} |")
    lines.append("")

    # Current Status
    if current_status:
        lines.append("## Current Status\n")
        lines.append(f"{current_status}\n")

    # Notable Features
    if stable_attrs:
        lines.append("## Notable Features\n")
        lines.append("| Feature | Value | Source | Confidence |")
        lines.append("|---|---|---|---|")
        for key, attr in stable_attrs.items():
            if isinstance(attr, dict):
                val = _format_attr_value(attr.get("value", ""))
                source = attr.get("source_turn", "")
                confidence = attr.get("confidence", "")
                inference = attr.get("inference", False)
                conf_str = f"{confidence}"
                if inference:
                    conf_str += " (inferred)"
            else:
                val = _format_attr_value(attr)
                source = ""
                conf_str = ""
            lines.append(f"| {key} | {val} | {source} | {conf_str} |")
        lines.append("")

    # Connected Entities
    if relationships:
        lines.append("## Connected Entities\n")
        lines.append("| Entity | Relationship | Type |")
        lines.append("|---|---|---|")
        for rel in relationships:
            target_id = rel.get("target_id", "")
            target_display = _resolve_target(target_id, name_index, "locations")
            cur_rel = _escape_table_cell(rel.get("current_relationship", ""))
            rel_type = _escape_table_cell(rel.get("type", ""))
            lines.append(f"| {target_display} | {cur_rel} | {rel_type} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated from [{eid}.json]({eid}.json) — do not edit manually.*")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Faction page
# ---------------------------------------------------------------------------

def generate_faction_page(entity: dict, name_index: dict[str, tuple[str, str]]) -> str:
    """Generate a wiki-style markdown page for a faction entity."""
    eid = entity.get("id", "")
    name = entity.get("name", eid)
    identity = entity.get("identity", "")
    first_seen = entity.get("first_seen_turn", "")
    last_updated = entity.get("last_updated_turn", "")
    current_status = entity.get("current_status", "")
    stable_attrs = entity.get("stable_attributes", {})
    relationships = entity.get("relationships", [])

    lines = []
    lines.append(f"# {name}\n")
    if identity:
        lines.append(f"> {identity}\n")

    # Infobox
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Type** | {_type_label(entity, 'Faction')} |")
    lines.append(f"| **First Seen** | {first_seen} |")
    lines.append(f"| **Last Updated** | {last_updated} |")
    for key, attr in stable_attrs.items():
        if key == "aliases":
            continue
        if isinstance(attr, dict):
            val = attr.get("value", "")
        else:
            val = attr
        display_val = _format_attr_value(val)
        if len(display_val) <= 80:
            lines.append(f"| **{key.replace('_', ' ').title()}** | {display_val} |")
    lines.append("")

    # Current Status
    if current_status:
        lines.append("## Current Status\n")
        lines.append(f"{current_status}\n")

    # Members / attributes
    if stable_attrs:
        lines.append("## Attributes\n")
        lines.append("| Attribute | Value | Source | Confidence |")
        lines.append("|---|---|---|---|")
        for key, attr in stable_attrs.items():
            if isinstance(attr, dict):
                val = _format_attr_value(attr.get("value", ""))
                source = attr.get("source_turn", "")
                confidence = attr.get("confidence", "")
                inference = attr.get("inference", False)
                conf_str = f"{confidence}"
                if inference:
                    conf_str += " (inferred)"
            else:
                val = _format_attr_value(attr)
                source = ""
                conf_str = ""
            lines.append(f"| {key} | {val} | {source} | {conf_str} |")
        lines.append("")

    # Relationships
    if relationships:
        lines.append("## Relationships\n")
        lines.append("| Entity | Relationship | Type | Status |")
        lines.append("|---|---|---|---|")
        for rel in relationships:
            target_id = rel.get("target_id", "")
            target_display = _resolve_target(target_id, name_index, "factions")
            cur_rel = _escape_table_cell(rel.get("current_relationship", ""))
            rel_type = _escape_table_cell(rel.get("type", ""))
            status = _escape_table_cell(rel.get("status", ""))
            lines.append(f"| {target_display} | {cur_rel} | {rel_type} | {status} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated from [{eid}.json]({eid}.json) — do not edit manually.*")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Item page
# ---------------------------------------------------------------------------

def generate_item_page(entity: dict, name_index: dict[str, tuple[str, str]]) -> str:
    """Generate a wiki-style markdown page for an item entity."""
    eid = entity.get("id", "")
    name = entity.get("name", eid)
    identity = entity.get("identity", "")
    first_seen = entity.get("first_seen_turn", "")
    last_updated = entity.get("last_updated_turn", "")
    current_status = entity.get("current_status", "")
    stable_attrs = entity.get("stable_attributes", {})
    volatile = entity.get("volatile_state", {})
    relationships = entity.get("relationships", [])

    lines = []
    lines.append(f"# {name}\n")
    if identity:
        lines.append(f"> {identity}\n")

    # Infobox
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Type** | {_type_label(entity, 'Item')} |")
    lines.append(f"| **First Seen** | {first_seen} |")
    lines.append(f"| **Last Updated** | {last_updated} |")
    for key, attr in stable_attrs.items():
        if key == "aliases":
            continue
        if isinstance(attr, dict):
            val = attr.get("value", "")
        else:
            val = attr
        display_val = _format_attr_value(val)
        if len(display_val) <= 80:
            lines.append(f"| **{key.replace('_', ' ').title()}** | {display_val} |")
    lines.append("")

    # Current Status
    if current_status:
        lines.append("## Current Status\n")
        lines.append(f"{current_status}\n")

    # Properties
    if stable_attrs:
        lines.append("## Properties\n")
        lines.append("| Property | Value | Source | Confidence |")
        lines.append("|---|---|---|---|")
        for key, attr in stable_attrs.items():
            if isinstance(attr, dict):
                val = _format_attr_value(attr.get("value", ""))
                source = attr.get("source_turn", "")
                confidence = attr.get("confidence", "")
                inference = attr.get("inference", False)
                conf_str = f"{confidence}"
                if inference:
                    conf_str += " (inferred)"
            else:
                val = _format_attr_value(attr)
                source = ""
                conf_str = ""
            lines.append(f"| {key} | {val} | {source} | {conf_str} |")
        lines.append("")

    # Current holder / location
    if volatile:
        holder_info = []
        condition = volatile.get("condition", "")
        location = volatile.get("location", "")
        if condition:
            holder_info.append(f"- **Condition:** {condition}")
        if location:
            holder_info.append(f"- **Location:** {location}")
        if holder_info:
            lines.append("## Current State\n")
            lines.extend(holder_info)
            lines.append("")

    # Relationships
    if relationships:
        lines.append("## Relationships\n")
        lines.append("| Entity | Relationship | Type |")
        lines.append("|---|---|---|")
        for rel in relationships:
            target_id = rel.get("target_id", "")
            target_display = _resolve_target(target_id, name_index, "items")
            cur_rel = _escape_table_cell(rel.get("current_relationship", ""))
            rel_type = _escape_table_cell(rel.get("type", ""))
            lines.append(f"| {target_display} | {cur_rel} | {rel_type} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated from [{eid}.json]({eid}.json) — do not edit manually.*")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Page generator dispatch
# ---------------------------------------------------------------------------

PAGE_GENERATORS = {
    "characters": generate_character_page,
    "locations": generate_location_page,
    "factions": generate_faction_page,
    "items": generate_item_page,
}


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

def generate_index_page(entity_type: str, entities: list[dict]) -> str:
    """Generate a README.md index page for an entity type directory."""
    label = TYPE_LABELS.get(entity_type, entity_type.title())
    title = f"{label}s" if not label.endswith("s") else label

    # Sort by first_seen_turn
    sorted_entities = sorted(entities, key=lambda e: _parse_turn_number(e.get("first_seen_turn", "")))

    lines = []
    lines.append(f"# {title}\n")

    if not sorted_entities:
        lines.append("*No entities cataloged yet.*\n")
        return "\n".join(lines) + "\n"

    lines.append("| Name | Current Status | First Seen | Last Updated | Relationships |")
    lines.append("|---|---|---|---|---|")

    for entity in sorted_entities:
        eid = entity.get("id", "")
        name = _escape_table_cell(entity.get("name", eid))
        status = _escape_table_cell(entity.get("current_status", ""))
        # Truncate status to 60 chars
        if len(status) > 60:
            status = status[:57] + "..."
        first_seen = entity.get("first_seen_turn", "")
        last_updated = entity.get("last_updated_turn", "")
        rel_count = len(entity.get("relationships", []))
        lines.append(f"| [{name}]({eid}.md) | {status} | {first_seen} | {last_updated} | {rel_count} |")

    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------

def generate_wiki_pages(catalog_dir: str, entity_types: list[str] | None = None,
                        index_only: bool = False) -> dict[str, int]:
    """Generate wiki-style markdown pages for entities in catalog_dir.

    Args:
        catalog_dir: Path to the catalogs directory (e.g. framework-local/catalogs/).
        entity_types: Optional list of types to generate (e.g. ["characters"]).
                      Defaults to all types.
        index_only: If True, only regenerate index pages.

    Returns:
        Dict mapping entity type to number of pages generated.
    """
    types_to_process = entity_types or ENTITY_TYPES
    all_entities = _load_all_entities(catalog_dir)
    name_index = _build_name_index(all_entities)
    stats: dict[str, int] = {}

    for entity_type in types_to_process:
        if entity_type not in ENTITY_TYPES:
            print(f"  WARNING: Unknown entity type '{entity_type}', skipping", file=sys.stderr)
            continue

        type_dir = os.path.join(catalog_dir, entity_type)
        if not os.path.isdir(type_dir):
            stats[entity_type] = 0
            continue

        entities = all_entities.get(entity_type, [])
        live_ids = {e.get("id") for e in entities if e.get("id")}
        page_count = 0

        # Generate individual entity pages
        if not index_only:
            generator = PAGE_GENERATORS.get(entity_type)
            if generator:
                for entity in entities:
                    eid = entity.get("id", "")
                    if not eid:
                        continue
                    md_content = generator(entity, name_index)
                    md_path = os.path.join(type_dir, f"{eid}.md")
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(md_content)
                    page_count += 1

            # Prune stale .md files whose entity JSON no longer exists
            for fname in os.listdir(type_dir):
                if fname == "README.md" or not fname.endswith(".md"):
                    continue
                stem = fname[:-3]  # strip .md
                if stem not in live_ids:
                    stale_path = os.path.join(type_dir, fname)
                    os.remove(stale_path)
                    print(f"  Pruned stale wiki page: {fname}", file=sys.stderr)

        # Generate index page
        index_content = generate_index_page(entity_type, entities)
        readme_path = os.path.join(type_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        page_count += 1

        stats[entity_type] = page_count
        print(f"  {entity_type}: {page_count} pages generated")

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate wiki-style markdown pages from per-entity JSON catalogs."
    )
    parser.add_argument(
        "--framework", required=True,
        help="Path to the framework directory (e.g. framework-local/)"
    )
    parser.add_argument(
        "--type", dest="entity_type",
        choices=ENTITY_TYPES,
        help="Limit generation to one entity type"
    )
    parser.add_argument(
        "--index-only", action="store_true",
        help="Only regenerate index pages, not individual entity pages"
    )
    args = parser.parse_args()

    catalog_dir = os.path.join(args.framework, "catalogs")
    if not os.path.isdir(catalog_dir):
        print(f"ERROR: Catalog directory not found: {catalog_dir}", file=sys.stderr)
        sys.exit(1)

    types = [args.entity_type] if args.entity_type else None
    stats = generate_wiki_pages(catalog_dir, entity_types=types, index_only=args.index_only)

    total = sum(stats.values())
    print(f"\nTotal: {total} wiki pages generated.")


if __name__ == "__main__":
    main()
