"""Parallel retry of previously-failed extraction turns.

Reads the extraction log to identify turns where discovery_ok=False,
then re-extracts them using a ThreadPoolExecutor. Each worker runs
the full extract_and_merge pipeline independently with its own catalog
snapshot. After all workers complete, entity results are merged
sequentially into the shared catalog and saved to disk.

Concurrency notes:
    The default is --workers 1 (sequential turns). This is intentional.
    Each turn already uses parallel_workers from config (typically 4)
    to parallelize internal phases (detail, PC, relationships, events).
    
    With parallel_workers=4 in config, each turn generates up to 4
    simultaneous LLM requests to the server's batch queue. The server
    (ContinuousBatchingPipeline) processes batches atomically — while
    one batch generates, new requests queue in memory.
    
    Increasing --workers multiplies with parallel_workers:
      --workers 2 × parallel_workers 4 = 8 max concurrent requests
      --workers 4 × parallel_workers 4 = 16 max concurrent requests
    
    At batch=8 with discovery_max_tokens=8192, per-request throughput
    drops to ~24 tok/s, making 8192/24 = 341s > 300s timeout.
    This causes cascading timeout failures.
    
    Safe configurations:
      --workers 1, parallel_workers=4  (proven, ~63s/turn avg)
      --workers 2, parallel_workers=2  (untested, marginal)
      --workers 4, parallel_workers=1  (untested, loses batching benefit)

Usage:
    python tools/retry_failed_turns.py --session sessions/openvino-test

Options:
    --session       Path to session directory (required)
    --framework     Path to framework directory (default: framework)
    --workers       Number of parallel workers (default: 1)
    --dry-run       Print what would be done without making LLM calls
"""

import argparse
import copy
import json
import os
import sys
import time
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catalog_merger import load_catalogs, load_events, save_catalogs, save_events, merge_entity
from llm_client import LLMClient, LLMExtractionError, QuotaExhaustedError
from semantic_extraction import (
    extract_and_merge,
    _write_extraction_log,
    _reset_pc_failure_tracking,
)
from temporal_extraction import load_timeline, save_timeline


def load_failed_turns(extraction_log_path: str) -> list[str]:
    """Read extraction log and return turn IDs where discovery failed.

    A turn is considered failed if its most recent log entry has
    discovery_ok=False. If a turn has both a failure and a later success,
    the success wins (it's already been retried successfully).
    """
    succeeded: set[str] = set()
    failed: set[str] = set()

    if not os.path.exists(extraction_log_path):
        return []

    with open(extraction_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            turn_id = entry.get("turn_id", "")
            if entry.get("discovery_ok"):
                succeeded.add(turn_id)
                failed.discard(turn_id)
            else:
                if turn_id not in succeeded:
                    failed.add(turn_id)

    return sorted(failed)


def load_turn_dicts(session_dir: str) -> list[dict]:
    """Load all turn dicts from transcript files."""
    turn_files = sorted(glob.glob(os.path.join(session_dir, "transcript", "turn-*.md")))
    turn_dicts = []
    for tf in turn_files:
        basename = os.path.basename(tf)
        # Format: turn-NNN-speaker.md
        parts = basename.replace(".md", "").split("-")
        turn_id = f"turn-{parts[1]}"
        speaker = parts[2] if len(parts) > 2 else "dm"
        with open(tf, encoding="utf-8") as f:
            text = f.read()
        # Strip the header line (## turn-NNN [speaker])
        lines = text.strip().split("\n")
        if lines and lines[0].startswith("##"):
            lines = lines[1:]
        text = "\n".join(lines).strip()
        turn_dicts.append({"turn_id": turn_id, "speaker": speaker, "text": text})
    return turn_dicts


def extract_single_turn(
    turn: dict,
    catalogs_snapshot: dict,
    events_snapshot: list,
    timeline_snapshot: list,
    config_path: str,
    overrides: dict | None,
    min_confidence: float,
    catalog_dir: str,
) -> tuple[str, dict, list, list, bool, dict]:
    """Extract a single turn in isolation. Thread-safe.

    Returns: (turn_id, catalogs, events, timeline, failed, log_record)
    """
    # Each thread gets its own LLM client (own HTTP session)
    llm = LLMClient(config_path, overrides=overrides)
    # Deep copy catalogs so merge operations don't interfere between threads
    local_catalogs = copy.deepcopy(catalogs_snapshot)
    local_events = copy.deepcopy(events_snapshot)
    local_timeline = copy.deepcopy(timeline_snapshot) if timeline_snapshot else []

    catalogs_out, events_out, failed, log_record = extract_and_merge(
        turn,
        local_catalogs,
        local_events,
        llm,
        min_confidence,
        catalog_dir=catalog_dir,
        timeline=local_timeline,
    )
    return (turn["turn_id"], catalogs_out, events_out, local_timeline, failed, log_record)


def merge_parallel_results(
    base_catalogs: dict,
    base_events: list,
    base_timeline: list,
    results: list[tuple[str, dict, list, list, bool, dict]],
) -> tuple[dict, list, list]:
    """Merge entity additions from parallel extractions into base catalogs.

    Each result contains a full catalog snapshot. We diff against the base
    to find new/updated entities and merge them sequentially.
    """
    base_entity_ids: dict[str, set[str]] = {}
    for key, entities in base_catalogs.items():
        base_entity_ids[key] = {e.get("id", "") for e in entities}

    for turn_id, result_catalogs, result_events, result_timeline, failed, log_record in results:
        if failed:
            # Don't merge data from failed extractions
            continue

        # Merge new/updated entities from this turn's result
        for key, entities in result_catalogs.items():
            for entity in entities:
                eid = entity.get("id", "")
                if not eid:
                    continue
                # merge_entity handles both new and updated entities
                merge_entity(base_catalogs, entity)

        # Merge new events (deduplicate by event ID)
        existing_event_ids = {e.get("id", "") for e in base_events}
        for event in result_events:
            if event.get("id", "") not in existing_event_ids:
                base_events.append(event)
                existing_event_ids.add(event.get("id", ""))

        # Merge timeline entries (deduplicate by turn + signal)
        existing_signals = {
            (t.get("turn_id", ""), t.get("signal", ""))
            for t in base_timeline
        }
        for entry in result_timeline:
            key = (entry.get("turn_id", ""), entry.get("signal", ""))
            if key not in existing_signals:
                base_timeline.append(entry)
                existing_signals.add(key)

    return base_catalogs, base_events, base_timeline


def main():
    parser = argparse.ArgumentParser(
        description="Parallel retry of failed extraction turns."
    )
    parser.add_argument("--session", required=True, help="Path to session directory")
    parser.add_argument("--framework", default="framework", help="Framework directory")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="External parallel workers (default: 1). WARNING: values >1 multiply "
             "with internal parallel_workers from config. With parallel_workers=4 "
             "and discovery_max_tokens=8192, more than 1 external worker risks "
             "timeouts due to server batch queue saturation. Only increase if you "
             "have also reduced parallel_workers in config."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--config", default="config/llm.json", help="LLM config path")
    parser.add_argument("--min-confidence", type=float, default=0.4)
    args = parser.parse_args()

    catalog_dir = os.path.join(args.framework, "catalogs")
    extraction_log_path = os.path.join(args.framework, "extraction-log.jsonl")

    # Determine worker count
    num_workers = args.workers
    if num_workers > 1:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = json.load(f)
            internal_parallel = config.get("parallel_workers", 1)
            max_concurrent = num_workers * internal_parallel
            if max_concurrent > 4:
                print(
                    f"  WARNING: {num_workers} external × {internal_parallel} internal "
                    f"= {max_concurrent} max concurrent requests. "
                    f"Server batch saturation may cause timeouts.",
                    file=sys.stderr,
                )
        except (OSError, json.JSONDecodeError):
            pass

    # Load failed turns from extraction log
    failed_turn_ids = load_failed_turns(extraction_log_path)
    if not failed_turn_ids:
        print("No failed turns to retry.")
        return

    print(f"Found {len(failed_turn_ids)} failed turn(s) to retry.")
    print(f"Workers: {num_workers}")

    # Load turn content
    all_turns = load_turn_dicts(args.session)
    turn_map = {t["turn_id"]: t for t in all_turns}

    # Filter to only failed turns that exist in the session
    retry_turns = []
    for tid in failed_turn_ids:
        if tid in turn_map:
            retry_turns.append(turn_map[tid])
        else:
            print(f"  WARNING: {tid} not found in session transcripts, skipping")

    if not retry_turns:
        print("No matching turns found in session.")
        return

    print(f"Retrying {len(retry_turns)} turn(s): {retry_turns[0]['turn_id']} ... {retry_turns[-1]['turn_id']}")

    if args.dry_run:
        for t in retry_turns:
            print(f"  [DRY RUN] Would retry: {t['turn_id']}")
        return

    # Load current catalogs as the base snapshot for context
    _reset_pc_failure_tracking()
    catalogs = load_catalogs(catalog_dir)
    events_list = load_events(catalog_dir)
    timeline = load_timeline(catalog_dir)

    entity_count_before = sum(len(v) for v in catalogs.values())
    event_count_before = len(events_list)

    print(f"Base catalogs: {entity_count_before} entities, {event_count_before} events")
    print(f"\nStarting parallel extraction...")
    print("-" * 60)

    t_start = time.monotonic()
    results: list[tuple[str, dict, list, list, bool, dict]] = []
    succeeded = 0
    still_failed = 0

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        future_to_turn = {}
        for turn in retry_turns:
            future = pool.submit(
                extract_single_turn,
                turn,
                catalogs,
                events_list,
                timeline,
                args.config,
                None,  # no overrides
                args.min_confidence,
                catalog_dir,
            )
            future_to_turn[future] = turn["turn_id"]

        for future in as_completed(future_to_turn):
            turn_id = future_to_turn[future]
            try:
                result = future.result()
                _, _, _, _, failed, log_record = result
                results.append(result)

                # Write extraction log immediately (append-only, thread-safe on most OS)
                _write_extraction_log(extraction_log_path, log_record)

                elapsed_s = log_record.get("elapsed_ms", 0) / 1000
                if failed:
                    still_failed += 1
                    err = log_record.get("discovery_error", "unknown")
                    print(f"  FAILED  {turn_id} ({elapsed_s:.1f}s) — {err}")
                else:
                    succeeded += 1
                    new_ent = log_record.get("new_entities", 0)
                    new_evt = log_record.get("new_events", 0)
                    print(f"  OK      {turn_id} ({elapsed_s:.1f}s) +{new_ent} entities, +{new_evt} events")

            except QuotaExhaustedError as e:
                still_failed += 1
                print(f"  QUOTA   {turn_id} — {e}")
                # Log the failure
                _write_extraction_log(extraction_log_path, {
                    "turn_id": turn_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "discovery_ok": False,
                    "discovery_error": f"QuotaExhaustedError: {e}",
                    "detail_ok": False, "detail_error": None,
                    "pc_ok": False, "pc_error": None,
                    "relationships_ok": False, "relationships_error": None,
                    "events_ok": False, "events_error": None,
                    "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                })
            except Exception as e:
                still_failed += 1
                print(f"  ERROR   {turn_id} — {type(e).__name__}: {e}")
                _write_extraction_log(extraction_log_path, {
                    "turn_id": turn_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "discovery_ok": False,
                    "discovery_error": f"{type(e).__name__}: {e}",
                    "detail_ok": False, "detail_error": None,
                    "pc_ok": None, "pc_error": None,
                    "relationships_ok": None, "relationships_error": None,
                    "events_ok": None, "events_error": None,
                    "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                })

    t_elapsed = time.monotonic() - t_start
    print("-" * 60)
    print(f"Completed in {t_elapsed:.1f}s")
    print(f"  Succeeded: {succeeded}/{len(retry_turns)}")
    print(f"  Failed:    {still_failed}/{len(retry_turns)}")

    # Merge all successful results into the base catalogs
    if succeeded > 0:
        print(f"\nMerging results into catalogs...")
        # Sort results by turn number for deterministic merge order
        results.sort(key=lambda r: r[0])
        catalogs, events_list, timeline = merge_parallel_results(
            catalogs, events_list, timeline, results
        )

        entity_count_after = sum(len(v) for v in catalogs.values())
        event_count_after = len(events_list)

        print(f"  Entities: {entity_count_before} → {entity_count_after} (+{entity_count_after - entity_count_before})")
        print(f"  Events:   {event_count_before} → {event_count_after} (+{event_count_after - event_count_before})")

        # Save to disk
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        save_timeline(catalog_dir, timeline)
        print("  Saved to disk.")

    # Update progress file to clear the failed_turns list
    progress_file = os.path.join(args.session, "derived", "extraction-progress.json")
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                progress = json.load(f)
            # Remove turns that succeeded from the failed list
            old_failed = set(progress.get("failed_turns", []))
            succeeded_ids = {r[0] for r in results if not r[4]}  # r[4] is 'failed' flag
            new_failed = sorted(old_failed - succeeded_ids)
            progress["failed_turns"] = new_failed
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(progress, f, indent=2)
            print(f"  Updated progress file: {len(old_failed)} → {len(new_failed)} failed turns")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not update progress file: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
