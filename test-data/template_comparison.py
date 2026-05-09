"""A/B/C/D/E test: run multiple template variants on the same turns with multiple runs each.
Usage: python test-data/template_comparison.py [config] [runs_per_turn]
"""
import subprocess, json, sys, os

TURNS = [201, 221, 251]
TEMPLATES = {
    "v1": "templates/extraction/entity-discovery.md",
    "v2": "templates/extraction/entity-discovery-v2.md",
    "v3": "templates/extraction/entity-discovery-v3.md",
    "v4": "templates/extraction/entity-discovery-v4.md",
    "v5": "templates/extraction/entity-discovery-v5.md",
}
CONFIG = sys.argv[1] if len(sys.argv) > 1 else "test-data/llm-rtx4070.json"
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUTPUT = f"test-data/template-comparison.json"

all_results = []

for variant, template in TEMPLATES.items():
    for turn in TURNS:
        out_file = f"test-data/_cmp_{variant}_t{turn}.json"
        cmd = [
            sys.executable, "-u", "tools/discovery_baseline.py",
            "--turns", str(turn),
            "--runs", str(RUNS),
            "--template", template,
            "--config", CONFIG,
            "--output-json", out_file,
        ]
        print(f"\n{'='*60}")
        print(f"  {variant} | Turn {turn} | {RUNS} runs")
        print(f"{'='*60}", flush=True)
        r = subprocess.run(cmd, capture_output=False, text=True)
        if r.returncode != 0:
            print(f"  FAILED (exit {r.returncode})")
            continue
        try:
            with open(out_file, "r") as f:
                data = json.load(f)
            for entry in data:
                entry["variant"] = variant
            all_results.extend(data)
        except Exception as e:
            print(f"  Error reading {out_file}: {e}")

with open(OUTPUT, "w") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

# Summary table
print(f"\n{'='*80}")
print(f"COMPARISON SUMMARY")
print(f"{'='*80}")
print(f"{'Variant':<8} {'Turn':<6} {'Runs':<5} {'Avg Ent':<9} {'Avg Act':<9} {'Avg Pas':<9} {'Avg Spur':<9} {'Avg Tok':<9} {'Avg Time':<9}")
print("-" * 80)

for variant in TEMPLATES:
    for turn in TURNS:
        entries = [e for e in all_results if e.get("variant") == variant and e["turn"] == turn and e["success"]]
        if not entries:
            print(f"{variant:<8} {turn:<6} {'0':<5} {'N/A':<9} {'N/A':<9} {'N/A':<9} {'N/A':<9} {'N/A':<9} {'N/A':<9}")
            continue
        n = len(entries)
        avg = lambda k: sum(e[k] for e in entries) / n
        print(f"{variant:<8} {turn:<6} {n:<5} {avg('entity_count'):<9.1f} {avg('active'):<9.1f} {avg('passive'):<9.1f} {avg('spurious'):<9.1f} {avg('output_tokens_est'):<9.0f} {avg('elapsed_s'):<9.1f}")

# Per-variant totals
print("-" * 80)
for variant in TEMPLATES:
    entries = [e for e in all_results if e.get("variant") == variant and e["success"]]
    if not entries:
        continue
    n = len(entries)
    avg_tok = sum(e["output_tokens_est"] for e in entries) / n
    avg_ent = sum(e["entity_count"] for e in entries) / n
    avg_spur = sum(e["spurious"] for e in entries) / n
    total_tok = sum(e["output_tokens_est"] for e in entries)
    print(f"{variant:<8} {'TOTAL':<6} {n:<5} {avg_ent:<9.1f} {'':<9} {'':<9} {avg_spur:<9.1f} {avg_tok:<9.0f}")

print(f"\nResults written to {OUTPUT}")
