"""Inline temperature sweep — single process, single LLM client, no subprocess spawning.

Usage: python test-data/temp_sweep_inline.py [config]

Runs from project root: python test-data/temp_sweep_inline.py test-data/llm-b70-sweep.json
"""
import json, os, sys, time, statistics
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from tools.catalog_merger import load_catalogs, format_known_entities_bounded, _estimate_tokens
from tools.discovery_baseline import load_turn, run_discovery, categorize_entities
from tools.llm_client import LLMClient

CONFIG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJECT_ROOT, "config", "llm.json")
TEMPLATE = os.path.join(PROJECT_ROOT, "templates", "extraction", "entity-discovery-v5.md")
TURNS = [201, 221, 251]
TEMPS = [0.05, 0.1, 0.2, 0.3]
RUNS_PER = 3
OUTPUT = os.path.join(PROJECT_ROOT, "test-data", "temp-sweep.json")

CATALOG_DIR = os.path.join(PROJECT_ROOT, "test-data", "catalogs")
TRANSCRIPT_DIR = os.path.join(PROJECT_ROOT, "test-data", "transcript")

# Load once
print(f"Loading catalogs from {CATALOG_DIR}...")
catalogs = load_catalogs(CATALOG_DIR)
total_entities = sum(len(v) for v in catalogs.values())
print(f"  Loaded {total_entities} entities")

with open(TEMPLATE, "r", encoding="utf-8") as f:
    system_prompt = f.read()
print(f"  Template: {os.path.basename(TEMPLATE)} ({_estimate_tokens(system_prompt)} tokens est.)")

with open(CONFIG, "r", encoding="utf-8") as f:
    config = json.load(f)

context_length = config.get("context_length", 32768)
discovery_max_tokens = config.get("discovery_max_tokens", config.get("max_tokens", 4096))

# Create a single LLM client
llm = LLMClient(CONFIG)
print(f"  LLM: {config.get('base_url')} / {config.get('model')}")

# Pre-load all turns and build known entities
turns_data = {}
for turn_num in TURNS:
    turn = load_turn(TRANSCRIPT_DIR, turn_num)
    known = format_known_entities_bounded(
        catalogs, current_turn=turn_num,
        context_length=context_length,
        turn_text=turn["text"],
    )
    turns_data[turn_num] = {"turn": turn, "known_entities": known}
    print(f"  Turn {turn_num}: {_estimate_tokens(turn['text'])} turn tokens, "
          f"{_estimate_tokens(known)} known-entity tokens")

all_results = []

for temp in TEMPS:
    print(f"\n{'='*60}")
    print(f"TEMPERATURE {temp}")
    print(f"{'='*60}")
    for turn_num in TURNS:
        td = turns_data[turn_num]
        for run in range(1, RUNS_PER + 1):
            print(f"\n  temp={temp} turn={turn_num} run={run}/{RUNS_PER}", flush=True)
            result = run_discovery(
                llm, td["turn"], td["known_entities"], system_prompt,
                max_tokens=discovery_max_tokens, temperature=temp,
            )
            if result["success"]:
                cats = categorize_entities(result["entities"], td["turn"]["text"])
                result["active"] = len(cats["active"])
                result["passive"] = len(cats["passive"])
                result["spurious"] = len(cats["spurious"])
                spur_names = [e.get("name", "?") for e in cats["spurious"]]
                print(f"  [OK] {result['elapsed_s']}s | entities: {result['entity_count']} "
                      f"(active={result['active']}, passive={result['passive']}, spurious={result['spurious']}) "
                      f"| output: ~{result['output_tokens_est']} tokens")
                if spur_names:
                    print(f"    Spurious: {', '.join(spur_names)}")
            else:
                result["active"] = result["passive"] = result["spurious"] = 0
                print(f"  [FAIL] {result['elapsed_s']}s: {result['error']}")
            
            result["temperature"] = temp
            result["turn"] = turn_num
            result["run"] = run
            all_results.append(result)

# Save raw results
with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

# Summary
print(f"\n{'='*80}")
print(f"TEMPERATURE SWEEP SUMMARY (v5 template, {RUNS_PER} runs/temp/turn)")
print(f"{'='*80}")
print(f"{'Temp':>6} | {'Turn':>5} | {'Tokens (avg)':>12} | {'Entities (avg)':>14} | {'Spurious (avg)':>14} | {'StdDev tok':>10}")

by_temp_turn = defaultdict(list)
by_temp = defaultdict(list)

for r in all_results:
    if r.get("success"):
        by_temp_turn[(r["temperature"], r["turn"])].append(r)
        by_temp[r["temperature"]].append(r)

for temp in TEMPS:
    for turn_num in TURNS:
        runs = by_temp_turn.get((temp, turn_num), [])
        if not runs:
            print(f"{temp:>6.2f} | {turn_num:>5} | {'N/A':>12} | {'N/A':>14} | {'N/A':>14} | {'N/A':>10}")
            continue
        toks = [r["output_tokens_est"] for r in runs]
        ents = [r["entity_count"] for r in runs]
        spur = [r["spurious"] for r in runs]
        std = statistics.stdev(toks) if len(toks) > 1 else 0
        print(f"{temp:>6.2f} | {turn_num:>5} | {statistics.mean(toks):>12.0f} | {statistics.mean(ents):>14.1f} | {statistics.mean(spur):>14.1f} | {std:>10.0f}")

print(f"\n{'Temp':>6} | {'Avg Tokens':>10} | {'Avg Entities':>12} | {'Avg Spurious':>12} | {'Token StdDev':>12} | {'CoeffVar':>8}")
print(f"{'-'*6}-+-{'-'*10}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}-+-{'-'*8}")
for temp in TEMPS:
    runs = by_temp.get(temp, [])
    if not runs:
        continue
    toks = [r["output_tokens_est"] for r in runs]
    ents = [r["entity_count"] for r in runs]
    spur = [r["spurious"] for r in runs]
    mean_t = statistics.mean(toks)
    std_t = statistics.stdev(toks) if len(toks) > 1 else 0
    cv = (std_t / mean_t * 100) if mean_t > 0 else 0
    print(f"{temp:>6.2f} | {mean_t:>10.0f} | {statistics.mean(ents):>12.1f} | {statistics.mean(spur):>12.1f} | {std_t:>12.0f} | {cv:>7.1f}%")

print(f"\nRaw results: {OUTPUT}")
