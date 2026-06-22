#!/usr/bin/env python3
"""
derive_planning_layer.py — Synthesize catalog data into derived planning files.

Bridges the gap between extracted catalog data (entities, events, timelines,
plot threads) and the derived planning layer (state.json, evidence.json,
timeline.json) used by analyze_next_move.py for strategic analysis.

This tool is intended to run after extraction populates the framework catalogs.
It reads from per-entity catalog files and produces actionable derived outputs
that feed into the analysis pipeline.

Usage:
    python tools/derive_planning_layer.py --session sessions/session-001 --framework framework/
    python tools/derive_planning_layer.py --session sessions/session-001 --framework framework/ --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from build_context import (
    load_entity_file,
    load_indexes,
    parse_turn_number,
)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _load_json(path: str, default=None):
    """Load JSON from a file, returning *default* if missing or invalid."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"Warning: failed to parse JSON from {path}: {exc}",
            file=sys.stderr,
        )
        return default


def _write_json(path: str, data, *, dry_run: bool = False) -> None:
    """Write JSON to *path*."""
    if dry_run:
        print(f"  [DRY] would write {path}")
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _next_seq(items: list[dict], prefix: str) -> int:
    """Return the next free sequence number for IDs like ``prefix-NNN``."""
    pattern = re.compile(r"^" + re.escape(prefix) + r"-(\d+)$")
    max_seq = 0
    for item in items:
        m = pattern.match(item.get("id", ""))
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def _load_turns(session_dir: str) -> list[dict] | None:
    """Load transcript turns without importing update_state (avoids cyclic import)."""
    transcript_dir = os.path.join(session_dir, "transcript")
    if not os.path.isdir(transcript_dir):
        return None
    pattern = re.compile(r"^turn-(\d+)-(player|dm)\.md$")
    turns: list[dict] = []
    for fname in sorted(os.listdir(transcript_dir)):
        m = pattern.match(fname)
        if m:
            seq = int(m.group(1))
            turns.append({
                "turn_id": f"turn-{seq:03d}",
                "sequence_number": seq,
                "speaker": m.group(2),
            })
    return turns or None


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------

def _load_all_entities(
    catalog_dir: str,
    id_lookup: dict[str, dict],
) -> list[dict]:
    """Load every full entity record from per-entity files."""
    entities: list[dict] = []
    for eid in id_lookup:
        entity = load_entity_file(catalog_dir, eid, id_lookup)
        if entity:
            entities.append(entity)
    return entities


def find_player_entity(
    catalog_dir: str,
    id_lookup: dict[str, dict],
) -> dict | None:
    """Locate the player character entity by convention.

    Heuristics (in order):
    1. ID is exactly ``char-player``
    2. ID starts with ``char-player``
    3. Entity name contains ``player`` (case-insensitive)
    """
    if "char-player" in id_lookup:
        return load_entity_file(catalog_dir, "char-player", id_lookup)

    for eid in sorted(id_lookup):
        if eid.startswith("char-player"):
            return load_entity_file(catalog_dir, eid, id_lookup)

    for eid, entry in id_lookup.items():
        if "player" in entry.get("name", "").lower():
            return load_entity_file(catalog_dir, eid, id_lookup)

    return None


def _is_placeholder(value) -> bool:
    """Return True if *value* is a TODO placeholder or empty default."""
    if not value or not isinstance(value, str):
        return True
    placeholders = {
        "unknown",
        "not established",
        "no npcs contacted yet",
    }
    return value.lower().strip() in placeholders or value.startswith("TODO:")


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------

def derive_state(
    session_dir: str,
    catalog_dir: str,
    entities: list[dict],
    id_lookup: dict[str, dict],
    plot_threads: list[dict],
    turns: list[dict] | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Populate ``state.json`` from catalog data.

    By default only replaces placeholder / empty values — preserves manually
    authored content (including repairing ``active_threads: null`` -> ``[]``).

    When *force* is True the catalog-derived fields (``current_world_state``,
    the player_state catalog fields, ``active_threads`` and
    ``known_constraints``) are regenerated to ALWAYS reflect the current
    catalogs.  A stale catalog-derived value is never preserved as if it were
    current: when the catalogs yield nothing for a field it is reset to the
    established honest placeholder (which clearly signals "not current" and
    self-heals on the next run).  A catalog that is UNAVAILABLE (input is
    ``None``) is distinguished from one that is merely EMPTY (an empty list):
    only the unavailable case preserves the prior value.  Returns the updated
    state dict.
    """
    derived_dir = os.path.join(session_dir, "derived")
    state_path = os.path.join(derived_dir, "state.json")
    state = _load_json(state_path, default={})

    # Ensure schema-required keys exist with safe defaults
    state.setdefault("as_of_turn", "turn-001")
    state.setdefault("current_world_state", "")
    state.setdefault("player_state", {})
    state.setdefault("active_threads", [])

    # as_of_turn
    if turns:
        state["as_of_turn"] = turns[-1]["turn_id"]

    # Iteration-safe view of the entity catalog.  ``entities is None`` is the
    # "catalog UNAVAILABLE" sentinel (distinct from an EMPTY list); the
    # known_constraints branch below keys off the original ``entities`` to
    # honour that distinction, while every catalog-scan loop iterates this
    # normalized list so an unavailable catalog never raises.
    entities_iter = entities if isinstance(entities, list) else []

    # -- current_world_state ---------------------------------------------------
    if force or _is_placeholder(state.get("current_world_state")):
        parts: list[str] = []

        # Temporal context
        temporal = state.get("temporal", {})
        if temporal.get("current_season"):
            season_str = temporal["current_season"].replace("_", " ").title()
            year_part = f" (Year {temporal['current_year']})" if temporal.get("current_year") else ""
            parts.append(f"Current time: {season_str}{year_part}.")

        # Location summaries
        for entity in entities_iter:
            if entity.get("type") == "location":
                status = entity.get("current_status")
                if isinstance(status, str) and status:
                    parts.append(f"{entity['name']}: {status}")

        if force:
            # Honest semantics: always reflect the current catalogs.  When the
            # catalogs yield nothing, reset to the self-healing placeholder
            # rather than preserving a stale value as if it were current.
            state["current_world_state"] = (
                " ".join(parts) if parts else "TODO: Update from transcript."
            )
        elif parts:
            state["current_world_state"] = " ".join(parts)

    # -- player_state ----------------------------------------------------------
    player = find_player_entity(catalog_dir, id_lookup)
    player_state = state.get("player_state", {})

    if player:
        vol = player.get("volatile_state")
        vol = vol if isinstance(vol, dict) else {}

        # -- location --
        loc_id = vol.get("location", "")
        loc_candidate = ""
        if isinstance(loc_id, str) and loc_id:
            loc_entry = id_lookup.get(loc_id)
            loc_candidate = loc_entry["name"] if loc_entry else loc_id
        if force:
            player_state["location"] = (
                loc_candidate if isinstance(loc_candidate, str) and loc_candidate
                else "Unknown"
            )
        elif _is_placeholder(player_state.get("location")):
            if loc_candidate:
                player_state["location"] = loc_candidate

        # -- condition --
        cond = vol.get("condition", "")
        cond_candidate = cond if isinstance(cond, str) else ""
        if force:
            player_state["condition"] = cond_candidate if cond_candidate else "Unknown"
        elif _is_placeholder(player_state.get("condition")):
            if cond_candidate:
                player_state["condition"] = cond_candidate

        # -- inventory_notes --
        equip = vol.get("equipment", [])
        if equip:
            inv_candidate = (
                ", ".join(str(e) for e in equip)
                if isinstance(equip, list)
                else str(equip)
            )
        else:
            inv_candidate = ""
        if force:
            player_state["inventory_notes"] = (
                inv_candidate if inv_candidate else "Not established"
            )
        elif _is_placeholder(player_state.get("inventory_notes")):
            if inv_candidate:
                player_state["inventory_notes"] = inv_candidate

        # -- relationships_summary --
        rels = player.get("relationships")
        rels = rels if isinstance(rels, list) else []
        active_rels = [
            r for r in rels
            if isinstance(r, dict) and r.get("status", "active") == "active"
        ]
        rel_candidate = ""
        if active_rels:
            rel_parts = []
            for r in active_rels:
                tid = r.get("target_id")
                tentry = id_lookup.get(tid) if isinstance(tid, str) else None
                tname = tentry["name"] if tentry else tid
                rel_parts.append(f"{tname}: {r.get('current_relationship', 'related')}")
            rel_candidate = "; ".join(rel_parts)
        if force:
            player_state["relationships_summary"] = (
                rel_candidate if rel_candidate else "No NPCs contacted yet"
            )
        elif _is_placeholder(player_state.get("relationships_summary")):
            if rel_candidate:
                player_state["relationships_summary"] = rel_candidate

    state["player_state"] = player_state

    # -- active_threads --------------------------------------------------------
    # Catalog AVAILABLE => plot_threads is a list (possibly empty).
    # Catalog UNAVAILABLE => plot_threads is None.
    if isinstance(plot_threads, list):
        active = [
            str(t["id"])
            for t in plot_threads
            if isinstance(t, dict)
            and t.get("status") == "active"
            and isinstance(t.get("id"), str)
        ]
    else:
        active = []

    if force:
        # Honest semantics: when the catalog is available, always reflect it
        # (even an empty list).  Only an UNAVAILABLE catalog (None) preserves
        # the existing value.
        if isinstance(plot_threads, list):
            state["active_threads"] = active
    elif not state.get("active_threads"):
        # Original repair semantics: null / empty -> [] (or recomputed list).
        state["active_threads"] = active if active else []

    # -- known_constraints (from explicit entity attributes) --------------------
    if force or not state.get("known_constraints"):
        known: list[str] = []
        entity_list = entities if isinstance(entities, list) else []
        for entity in entity_list:
            for attr_key, attr_val in entity.get("stable_attributes", {}).items():
                if not isinstance(attr_val, dict):
                    continue
                if attr_val.get("inference", False):
                    continue
                val = attr_val.get("value")
                source = attr_val.get("source_turn", "")
                if isinstance(val, str) and val and source:
                    known.append(
                        f"{entity['name']}'s {attr_key}: {val} (from {source})"
                    )
        if force:
            # Catalog available (entities is a list) -> always reflect it,
            # even when empty.  Unavailable -> preserve.
            if isinstance(entities, list):
                state["known_constraints"] = known
        elif known:
            state["known_constraints"] = known

    # -- inferred_constraints (from inferred entity attributes) -----------------
    if not state.get("inferred_constraints"):
        inferred: list[dict] = []
        for entity in entities_iter:
            for attr_key, attr_val in entity.get("stable_attributes", {}).items():
                if not isinstance(attr_val, dict):
                    continue
                if not attr_val.get("inference", False):
                    continue
                val = attr_val.get("value")
                conf = attr_val.get("confidence", 0.5)
                source = attr_val.get("source_turn", "")
                if not val or not source:
                    continue
                inferred.append({
                    "statement": f"{entity['name']}'s {attr_key} may be {val}",
                    "confidence": conf,
                    "source_turns": [source],
                })
        if inferred:
            state["inferred_constraints"] = inferred

    # -- risks (from adversarial relationships) --------------------------------
    if not state.get("risks"):
        risks: list[str] = []
        for entity in entities_iter:
            for r in entity.get("relationships", []):
                if r.get("type") == "adversarial" and r.get("status", "active") == "active":
                    rid = r["target_id"]
                    tentry = id_lookup.get(rid) if isinstance(rid, str) else None
                    tname = tentry["name"] if tentry else rid
                    desc = r.get("current_relationship", "conflict")
                    risks.append(
                        f"Adversarial: {entity['name']} vs {tname} — {desc}"
                    )
        if risks:
            state["risks"] = risks

    # -- opportunities (from active plot threads with open questions) -----------
    if not state.get("opportunities"):
        opps: list[str] = []
        for t in plot_threads if isinstance(plot_threads, list) else []:
            if not isinstance(t, dict) or t.get("status") != "active":
                continue
            for q in t.get("open_questions", []):
                opps.append(f"Investigate: {q}")
            if not t.get("open_questions") and t.get("title"):
                opps.append(f"Pursue thread: {t['title']}")
        if opps:
            state["opportunities"] = opps

    # -- persist ---------------------------------------------------------------
    _write_json(state_path, state, dry_run=dry_run)
    if not dry_run:
        print(f"  Updated: {state_path}")
    return state


# ---------------------------------------------------------------------------
# Evidence derivation
# ---------------------------------------------------------------------------

def _evidence_key(entry: dict) -> str:
    """Return a dedup key for an evidence entry."""
    turns_str = ",".join(sorted(entry.get("source_turns", [])))
    entities_str = ",".join(sorted(entry.get("related_entities", [])))
    return f"{entry.get('classification', '')}|{turns_str}|{entities_str}|{entry.get('statement', '')[:80]}"


def derive_evidence(
    session_dir: str,
    catalog_dir: str,
    entities: list[dict],
    events: list[dict],
    id_lookup: dict[str, dict],
    *,
    dry_run: bool = False,
) -> list[dict]:
    """Populate ``evidence.json`` from catalog events and entity attributes.

    Existing entries are preserved.  New entries are appended only if not
    already covered (by dedup key).  Returns the full evidence list.
    """
    derived_dir = os.path.join(session_dir, "derived")
    evidence_path = os.path.join(derived_dir, "evidence.json")
    existing = _load_json(evidence_path, default=[])
    existing_keys = {_evidence_key(e) for e in existing}

    seq = _next_seq(existing, "ev")
    new_entries: list[dict] = []

    # From catalog events → explicit_evidence
    for evt in events:
        source_turns = evt.get("source_turns", [])
        description = (evt.get("description") or "").strip()
        if not source_turns or not description:
            continue
        entry = {
            "id": f"ev-{seq:03d}",
            "statement": description,
            "classification": "explicit_evidence",
            "confidence": 1.0,
            "source_turns": source_turns,
            "related_entities": evt.get("related_entities", []),
            "related_threads": evt.get("related_threads", []),
            "notes": "auto-derived from catalog event",
        }
        if _evidence_key(entry) not in existing_keys:
            new_entries.append(entry)
            existing_keys.add(_evidence_key(entry))
            seq += 1

    # From entity attributes
    for entity in entities:
        for attr_key, attr_val in entity.get("stable_attributes", {}).items():
            if not isinstance(attr_val, dict):
                continue
            val = attr_val.get("value")
            if not val:
                continue

            source = attr_val.get("source_turn", "")
            source_turns = [source] if source else []
            if not source_turns:
                continue

            is_inference = attr_val.get("inference", False)
            if is_inference:
                raw_conf = attr_val.get("confidence", 0.5)
                try:
                    conf = float(raw_conf)
                except (TypeError, ValueError):
                    conf = 0.5
                conf = max(0.0, min(1.0, conf))
            else:
                conf = 1.0
            classification = "inference" if is_inference else "explicit_evidence"

            # Build value string
            if isinstance(val, list):
                val_str = ", ".join(str(v) for v in val)
            else:
                val_str = str(val)

            entry = {
                "id": f"ev-{seq:03d}",
                "statement": f"{entity['name']}: {attr_key} is {val_str}",
                "classification": classification,
                "confidence": conf,
                "source_turns": source_turns,
                "related_entities": [entity["id"]],
                "notes": "auto-derived from entity attribute",
            }
            if _evidence_key(entry) not in existing_keys:
                new_entries.append(entry)
                existing_keys.add(_evidence_key(entry))
                seq += 1

    # From entity relationships (inferred ones)
    for entity in entities:
        for rel in entity.get("relationships", []):
            conf = rel.get("confidence")
            if conf is None or conf >= 1.0:
                continue
            source = rel.get("first_seen_turn", "")
            if not source:
                continue

            rel_tid = rel["target_id"]
            tentry = id_lookup.get(rel_tid) if isinstance(rel_tid, str) else None
            tname = tentry["name"] if tentry else rel_tid
            desc = rel.get("current_relationship", "related")

            entry = {
                "id": f"ev-{seq:03d}",
                "statement": f"{entity['name']} may be {desc} {tname}",
                "classification": "inference",
                "confidence": conf,
                "source_turns": [source],
                "related_entities": [entity["id"], rel["target_id"]],
                "notes": "auto-derived from entity relationship",
            }
            if _evidence_key(entry) not in existing_keys:
                new_entries.append(entry)
                existing_keys.add(_evidence_key(entry))
                seq += 1

    all_evidence = existing + new_entries
    _write_json(evidence_path, all_evidence, dry_run=dry_run)
    if not dry_run:
        added = len(new_entries)
        print(f"  Updated: {evidence_path} ({len(existing)} existing + {added} new)")
    return all_evidence


# ---------------------------------------------------------------------------
# Timeline derivation
# ---------------------------------------------------------------------------

def derive_timeline(
    session_dir: str,
    catalog_timeline: list[dict],
    *,
    dry_run: bool = False,
) -> list[dict]:
    """Merge catalog-level and session-level timeline entries.

    Session-level entries (from pattern extraction) are preserved.  Catalog-level
    entries are added if not already covered (by source_turn + type + season).
    IDs are reassigned sequentially after merge.

    Returns the merged timeline list.
    """
    derived_dir = os.path.join(session_dir, "derived")
    timeline_path = os.path.join(derived_dir, "timeline.json")
    session_timeline = _load_json(timeline_path, default=[])

    # Build dedup keys from session timeline
    seen: set[tuple[str, str, str]] = set()
    for entry in session_timeline:
        key = (
            entry.get("source_turn", ""),
            entry.get("type", ""),
            entry.get("season", ""),
        )
        seen.add(key)

    # Add catalog entries not already present
    added = 0
    for entry in catalog_timeline:
        key = (
            entry.get("source_turn", ""),
            entry.get("type", ""),
            entry.get("season", ""),
        )
        if key not in seen:
            session_timeline.append(entry)
            seen.add(key)
            added += 1

    # Sort by turn number
    session_timeline.sort(
        key=lambda e: parse_turn_number(e.get("source_turn", ""))
    )

    # Reassign sequential IDs
    for i, entry in enumerate(session_timeline, 1):
        entry["id"] = f"time-{i:03d}"

    _write_json(timeline_path, session_timeline, dry_run=dry_run)
    if not dry_run:
        total = len(session_timeline)
        print(f"  Updated: {timeline_path} ({total} entries, {added} from catalog)")
    return session_timeline


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def derive_all(
    session_dir: str,
    framework_dir: str,
    turns: list[dict] | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Run all derivation steps.

    Returns a dict with keys ``state``, ``evidence``, ``timeline``.
    """
    catalog_dir = os.path.join(framework_dir, "catalogs")

    # Load catalog indexes
    _name_lookup, id_lookup = load_indexes(catalog_dir)

    if not id_lookup:
        print("  No catalog data found; derived planning layer unchanged.")
        return {"state": {}, "evidence": [], "timeline": []}

    # Load all entities once
    entities = _load_all_entities(catalog_dir, id_lookup)

    # Load catalog-level data
    events = _load_json(os.path.join(catalog_dir, "events.json"), default=[])
    catalog_timeline = _load_json(
        os.path.join(catalog_dir, "timeline.json"), default=[]
    )
    plot_threads = _load_json(
        os.path.join(catalog_dir, "plot-threads.json"), default=[]
    )

    print("Deriving planning layer from catalog data...")

    state = derive_state(
        session_dir, catalog_dir, entities, id_lookup, plot_threads,
        turns, dry_run=dry_run, force=force,
    )
    evidence = derive_evidence(
        session_dir, catalog_dir, entities, events, id_lookup,
        dry_run=dry_run,
    )
    timeline = derive_timeline(
        session_dir, catalog_timeline, dry_run=dry_run,
    )

    return {"state": state, "evidence": evidence, "timeline": timeline}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize catalog data into derived planning files.",
    )
    parser.add_argument(
        "--session", required=True,
        help="Path to session directory, e.g. sessions/session-001",
    )
    parser.add_argument(
        "--framework", required=True,
        help="Path to framework directory, e.g. framework/ or framework-local/",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written without modifying files.",
    )
    parser.add_argument(
        "--regen-from-catalogs", action="store_true",
        help=(
            "Force-regenerate catalog-derived state fields so they always "
            "reflect the current catalogs. When the catalogs yield nothing for "
            "a field it is reset to its honest placeholder (never preserved as "
            "stale-but-current); an unavailable catalog is left untouched. "
            "Overwrites prior catalog-derived values and manual edits; intended "
            "for automated per-turn refresh pipelines."
        ),
    )
    args = parser.parse_args()

    if not os.path.isdir(args.session):
        print(f"ERROR: Session directory not found: {args.session}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.framework):
        print(
            f"ERROR: Framework directory not found: {args.framework}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Optionally load turns for as_of_turn detection
    turns = _load_turns(args.session)

    result = derive_all(
        args.session, args.framework, turns, dry_run=args.dry_run,
        force=args.regen_from_catalogs,
    )

    state = result["state"]
    evidence = result["evidence"]
    timeline = result["timeline"]

    print(
        f"\nPlanning layer summary: "
        f"state={'populated' if state.get('current_world_state') else 'scaffold'}, "
        f"evidence={len(evidence)} entries, "
        f"timeline={len(timeline)} entries"
    )


if __name__ == "__main__":
    main()
