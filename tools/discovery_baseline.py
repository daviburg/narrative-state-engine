#!/usr/bin/env python3
"""Discovery baseline measurement harness for issue #310.

Loads catalogs and transcript data, runs entity discovery against a live LLM,
and reports output token consumption, entity counts, and categorization.

Usage:
    python tools/discovery_baseline.py --turns 200,210,220,250,300,306,312,340
    python tools/discovery_baseline.py --turns 210 --runs 3
    python tools/discovery_baseline.py --turns 210 --template templates/extraction/entity-discovery-v2.md
"""

import argparse
import json
import os
import re
import sys
import time

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tools.catalog_merger import (
    load_catalogs,
    format_known_entities_bounded,
    _estimate_tokens,
    _parse_turn_number,
)
from tools.semantic_extraction import format_discovery_prompt, load_template
from tools.llm_client import LLMClient


def load_turn(transcript_dir: str, turn_num: int) -> dict | None:
    """Load a DM turn file and return a turn dict."""
    filename = f"turn-{turn_num:03d}-dm.md"
    filepath = os.path.join(transcript_dir, filename)
    if not os.path.exists(filepath):
        print(f"  WARNING: {filepath} not found", file=sys.stderr)
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    return {
        "turn_id": f"turn-{turn_num:03d}",
        "speaker": "dm",
        "text": text,
    }


def categorize_entities(entities: list[dict], turn_text: str) -> dict:
    """Categorize discovered entities by involvement level.

    Returns dict with:
        active: entities that are new OR whose description suggests state change
        passive: entities found in turn text but no new info
        spurious: entities not referenced in turn text at all
    """
    turn_lower = turn_text.lower()
    active = []
    passive = []
    spurious = []

    for entity in entities:
        name = entity.get("name", "")
        is_new = entity.get("is_new", False)
        has_description = bool(entity.get("description", ""))

        # Check if entity name appears in turn text
        name_in_text = name.lower() in turn_lower if name else False

        # Also check existing_id stem (e.g., "char-kael" -> "kael")
        eid = entity.get("existing_id") or entity.get("proposed_id") or ""
        id_stem = eid.split("-", 1)[-1].replace("-", " ") if eid else ""
        id_in_text = id_stem.lower() in turn_lower if id_stem else False

        in_text = name_in_text or id_in_text

        if is_new or has_description:
            active.append(entity)
        elif in_text:
            passive.append(entity)
        else:
            spurious.append(entity)

    return {"active": active, "passive": passive, "spurious": spurious}


def run_discovery(
    llm: LLMClient,
    turn: dict,
    known_entities: str,
    system_prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict:
    """Run a single discovery call and return metrics.

    Returns dict with:
        success, entities, raw_text, output_tokens, elapsed_s, truncated, error
    """
    user_prompt = format_discovery_prompt(turn, known_entities)
    input_tokens = _estimate_tokens(system_prompt + user_prompt)

    print(f"  Calling LLM (max_tokens={max_tokens}, temp={temperature})...",
          flush=True)
    start = time.time()
    try:
        result = llm.extract_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        elapsed = time.time() - start
        entities = result.get("entities", []) if isinstance(result, dict) else []
        raw_text = json.dumps(result)
        output_tokens = _estimate_tokens(raw_text)

        return {
            "success": True,
            "entities": entities,
            "entity_count": len(entities),
            "raw_text": raw_text,
            "input_tokens_est": input_tokens,
            "output_tokens_est": output_tokens,
            "elapsed_s": round(elapsed, 1),
            "truncated": False,
            "error": None,
        }
    except Exception as e:
        elapsed = time.time() - start
        err_name = type(e).__name__
        partial = getattr(e, "partial_text", None)
        print(f"  LLM call failed after {elapsed:.1f}s: {err_name}", flush=True)
        return {
            "success": False,
            "entities": [],
            "entity_count": 0,
            "raw_text": partial or "",
            "input_tokens_est": input_tokens,
            "output_tokens_est": _estimate_tokens(partial) if partial else 0,
            "elapsed_s": round(elapsed, 1),
            "truncated": "Truncation" in err_name,
            "error": f"{err_name}: {e}",
        }


def main():
    parser = argparse.ArgumentParser(description="Discovery baseline measurement")
    parser.add_argument(
        "--turns", type=str, required=True,
        help="Comma-separated turn numbers to test (e.g., 200,210,220)",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Number of runs per turn (for consistency validation)",
    )
    parser.add_argument(
        "--template", type=str, default=None,
        help="Path to alternative discovery template (default: entity-discovery.md)",
    )
    parser.add_argument(
        "--catalog-dir", type=str,
        default=os.path.join(PROJECT_ROOT, "test-data", "catalogs"),
        help="Path to catalog directory",
    )
    parser.add_argument(
        "--transcript-dir", type=str,
        default=os.path.join(PROJECT_ROOT, "test-data", "transcript"),
        help="Path to transcript directory",
    )
    parser.add_argument(
        "--config", type=str,
        default=os.path.join(PROJECT_ROOT, "config", "llm.json"),
        help="Path to LLM config",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None,
        help="Override discovery max_tokens (default: from config discovery_max_tokens)",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="Override temperature (default: from config discovery_temperature)",
    )
    parser.add_argument(
        "--entity-context-budget", type=int, default=None,
        help="Override entity_context_budget (default: from config)",
    )
    parser.add_argument(
        "--output-json", type=str, default=None,
        help="Write detailed results to JSON file",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build prompts and report sizes without calling LLM",
    )
    args = parser.parse_args()

    turn_nums = [int(t.strip()) for t in args.turns.split(",")]

    # Load catalogs
    print(f"Loading catalogs from {args.catalog_dir}...")
    catalogs = load_catalogs(args.catalog_dir)
    total_entities = sum(len(v) for v in catalogs.values())
    print(f"  Loaded {total_entities} entities")

    # Load LLM config
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    context_length = config.get("context_length", 32768)
    entity_budget = args.entity_context_budget or config.get("entity_context_budget")
    discovery_max_tokens = args.max_tokens or config.get("discovery_max_tokens", config.get("max_tokens", 4096))
    discovery_temp = args.temperature if args.temperature is not None else config.get("discovery_temperature", config.get("temperature", 0.3))

    # Load template
    if args.template:
        with open(args.template, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        template_name = os.path.basename(args.template)
    else:
        system_prompt = load_template("entity-discovery")
        template_name = "entity-discovery.md"

    system_tokens = _estimate_tokens(system_prompt)
    print(f"  Template: {template_name} ({system_tokens} tokens est.)")
    print(f"  Context length: {context_length}, entity budget: {entity_budget}")
    print(f"  Discovery max_tokens: {discovery_max_tokens}, temperature: {discovery_temp}")

    # Initialize LLM client (only if not dry-run)
    llm = None
    if not args.dry_run:
        llm = LLMClient(args.config)
        print(f"  LLM: {config.get('base_url')} / {config.get('model')}")

    print(f"\n{'='*80}")

    all_results = []

    for turn_num in turn_nums:
        turn = load_turn(args.transcript_dir, turn_num)
        if turn is None:
            continue

        turn_text = turn["text"]
        turn_tokens = _estimate_tokens(turn_text)

        # Build known entities context
        known = format_known_entities_bounded(
            catalogs,
            current_turn=turn_num,
            context_length=context_length,
            entity_context_budget=entity_budget,
            turn_text=turn_text,
        )
        known_tokens = _estimate_tokens(known)

        # Count how many entities made it into the context
        entity_lines = [l for l in known.split("\n") if "|" in l and not l.startswith("(")]
        entities_in_context = len(entity_lines)

        print(f"\n--- Turn {turn_num:03d} ---", flush=True)
        print(f"  Turn text: {turn_tokens} tokens est.", flush=True)
        print(f"  Known entities in context: {entities_in_context} / {total_entities} ({known_tokens} tokens est.)", flush=True)
        print(f"  Total input: ~{system_tokens + turn_tokens + known_tokens} tokens est.", flush=True)

        if args.dry_run:
            all_results.append({
                "turn": turn_num,
                "turn_tokens": turn_tokens,
                "entities_in_context": entities_in_context,
                "known_entities_tokens": known_tokens,
                "total_input_tokens": system_tokens + turn_tokens + known_tokens,
            })
            continue

        for run_idx in range(args.runs):
            result = run_discovery(
                llm, turn, known, system_prompt,
                max_tokens=discovery_max_tokens,
                temperature=discovery_temp,
            )

            # Count and expand compact discovery entries from catalog (#310)
            compact_count = sum(
                1 for e in result["entities"]
                if e.get("existing_id") and not e.get("name")
            )
            for entity in result["entities"]:
                if entity.get("existing_id") and not entity.get("name"):
                    eid = entity["existing_id"]
                    cat_entry = None
                    for _fn, ents in catalogs.items():
                        for e in ents:
                            if e.get("id") == eid:
                                cat_entry = e
                                break
                        if cat_entry:
                            break
                    if cat_entry:
                        entity.setdefault("name", cat_entry.get("name", eid))
                        entity.setdefault("type", cat_entry.get("type", "concept"))
                    else:
                        entity.setdefault("name", eid)
                        entity.setdefault("type", "concept")
                    entity.setdefault("is_new", False)

            categories = categorize_entities(result["entities"], turn_text)

            run_label = f"  Run {run_idx+1}/{args.runs}" if args.runs > 1 else " "
            status = "OK" if result["success"] else "FAILED"
            if result["truncated"]:
                status = "TRUNCATED"

            print(f"{run_label} [{status}] {result['elapsed_s']}s | "
                  f"entities: {result['entity_count']} "
                  f"(active={len(categories['active'])}, "
                  f"passive={len(categories['passive'])}, "
                  f"spurious={len(categories['spurious'])}, "
                  f"compact={compact_count}) | "
                  f"output: ~{result['output_tokens_est']} tokens")

            if result["error"]:
                print(f"    ERROR: {result['error'][:200]}")

            # Show spurious entities (the waste)
            if categories["spurious"]:
                spurious_names = [e.get("name", "?") for e in categories["spurious"][:10]]
                extra = f" +{len(categories['spurious'])-10} more" if len(categories["spurious"]) > 10 else ""
                print(f"    Spurious: {', '.join(spurious_names)}{extra}")

            all_results.append({
                "turn": turn_num,
                "run": run_idx + 1,
                "success": result["success"],
                "truncated": result["truncated"],
                "elapsed_s": result["elapsed_s"],
                "entity_count": result["entity_count"],
                "active": len(categories["active"]),
                "passive": len(categories["passive"]),
                "spurious": len(categories["spurious"]),
                "input_tokens_est": result["input_tokens_est"],
                "output_tokens_est": result["output_tokens_est"],
                "error": result["error"],
                "entities": result["entities"],
                "categories": {
                    "active": [e.get("name") for e in categories["active"]],
                    "passive": [e.get("name") for e in categories["passive"]],
                    "spurious": [e.get("name") for e in categories["spurious"]],
                },
            })

    # Summary
    successful = [r for r in all_results if r.get("success")]
    if successful:
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"  Turns tested: {len(turn_nums)}")
        print(f"  Runs per turn: {args.runs}")
        avg_entities = sum(r["entity_count"] for r in successful) / len(successful)
        avg_tokens = sum(r["output_tokens_est"] for r in successful) / len(successful)
        avg_time = sum(r["elapsed_s"] for r in successful) / len(successful)
        avg_active = sum(r["active"] for r in successful) / len(successful)
        avg_passive = sum(r["passive"] for r in successful) / len(successful)
        avg_spurious = sum(r["spurious"] for r in successful) / len(successful)
        print(f"  Avg entities: {avg_entities:.1f} (active={avg_active:.1f}, passive={avg_passive:.1f}, spurious={avg_spurious:.1f})")
        print(f"  Avg output tokens: {avg_tokens:.0f}")
        print(f"  Avg elapsed: {avg_time:.1f}s")

        truncated = [r for r in all_results if r.get("truncated")]
        if truncated:
            print(f"  TRUNCATIONS: {len(truncated)}/{len(all_results)}")

    # Write detailed results
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        print(f"\nDetailed results written to {args.output_json}")


if __name__ == "__main__":
    main()
