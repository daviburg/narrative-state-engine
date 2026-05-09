"""Temperature sweep: run v5 discovery at multiple temperatures on B70 to find the sweet spot.

Usage: python test-data/temp_sweep.py [config] [runs_per_temp]
"""
import subprocess, json, sys, os, time

TEMPLATE = "templates/extraction/entity-discovery-v5.md"
CONFIG = sys.argv[1] if len(sys.argv) > 1 else "config/llm.json"
RUNS_PER_TEMP = int(sys.argv[2]) if len(sys.argv) > 2 else 3
TURNS = [201, 221, 251]
TEMPS = [0.05, 0.1, 0.2, 0.3]
OUTPUT = "test-data/temp-sweep.json"

all_results = []

for temp in TEMPS:
    print(f"\n{'='*60}")
    print(f"TEMPERATURE {temp}")
    print(f"{'='*60}")
    for turn in TURNS:
        for run in range(1, RUNS_PER_TEMP + 1):
            out_file = f"test-data/_sweep_t{temp}_turn{turn}_r{run}.json"
            cmd = [
                sys.executable, "-u", "tools/discovery_baseline.py",
                "--turns", str(turn),
                "--template", TEMPLATE,
                "--config", CONFIG,
                "--output-json", out_file,
                "--temperature", str(temp),
            ]
            label = f"  temp={temp} turn={turn} run={run}/{RUNS_PER_TEMP}"
            print(f"\n{label}", flush=True)
            r = subprocess.run(cmd, capture_output=False, text=True)
            if r.returncode != 0:
                print(f"  FAILED (exit {r.returncode})")
                continue
            try:
                with open(out_file, "r") as f:
                    data = json.load(f)
                for d in data:
                    d["temperature"] = temp
                    d["run"] = run
                all_results.extend(data)
            except Exception as e:
                print(f"  Error reading {out_file}: {e}")

# Save raw results
with open(OUTPUT, "w") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

# Summary table
print(f"\n{'='*80}")
print(f"TEMPERATURE SWEEP SUMMARY (v5 template, {RUNS_PER_TEMP} runs/temp/turn)")
print(f"{'='*80}")
print(f"{'Temp':>6} | {'Turn':>5} | {'Tokens (avg)':>12} | {'Entities (avg)':>14} | {'Spurious (avg)':>14} | {'StdDev tok':>10}")

from collections import defaultdict
import statistics

by_temp_turn = defaultdict(list)
by_temp = defaultdict(list)

for r in all_results:
    if r.get("success"):
        by_temp_turn[(r["temperature"], r["turn"])].append(r)
        by_temp[r["temperature"]].append(r)

for temp in TEMPS:
    for turn in TURNS:
        runs = by_temp_turn.get((temp, turn), [])
        if not runs:
            print(f"{temp:>6.2f} | {turn:>5} | {'N/A':>12} | {'N/A':>14} | {'N/A':>14} | {'N/A':>10}")
            continue
        toks = [r["output_tokens_est"] for r in runs]
        ents = [r["entity_count"] for r in runs]
        spur = [r["spurious"] for r in runs]
        std = statistics.stdev(toks) if len(toks) > 1 else 0
        print(f"{temp:>6.2f} | {turn:>5} | {statistics.mean(toks):>12.0f} | {statistics.mean(ents):>14.1f} | {statistics.mean(spur):>14.1f} | {std:>10.0f}")

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
