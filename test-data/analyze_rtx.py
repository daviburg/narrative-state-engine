"""Quick analysis of RTX comparison results."""
import json
from collections import defaultdict

with open("test-data/comparison-rtx.json") as f:
    data = json.load(f)

results = defaultdict(lambda: defaultdict(list))
for entry in data:
    key = (entry["turn"], entry["template"])
    results[key]["ents"].append(entry.get("entity_count", 0))
    results[key]["tok"].append(entry.get("output_tokens_est", 0))
    results[key]["spur"].append(entry.get("spurious", 0))
    results[key]["compact"].append(entry.get("compact_count", 0))
    results[key]["fail"].append(not entry.get("success", True))

print(f"{'Turn':>6} | {'--- V1 (original) ---':^30} | {'--- V5 (optimized) ---':^30}")
print(f"{'':>6} | {'AvgTok':>7} {'Ent':>6} {'Spur':>6} {'Fail':>5} | {'AvgTok':>7} {'Ent':>6} {'Spur':>6} {'Fail':>5}")
print("-" * 80)

turns = sorted(set(e["turn"] for e in data))
v1_total_tok = 0
v5_total_tok = 0
v1_total_spur = 0
v5_total_spur = 0
v1_total_fail = 0
v5_total_fail = 0
v1_runs = 0
v5_runs = 0

for turn_id in turns:
    v1 = results[(turn_id, "v1")]
    v5 = results[(turn_id, "v5")]
    v1_avg_tok = sum(v1["tok"]) // max(len(v1["tok"]), 1)
    v5_avg_tok = sum(v5["tok"]) // max(len(v5["tok"]), 1)
    v1_ent = f"{min(v1['ents'])}-{max(v1['ents'])}" if min(v1['ents']) != max(v1['ents']) else str(v1['ents'][0])
    v5_ent = f"{min(v5['ents'])}-{max(v5['ents'])}" if min(v5['ents']) != max(v5['ents']) else str(v5['ents'][0])
    v1_s = f"{min(v1['spur'])}-{max(v1['spur'])}" if min(v1['spur']) != max(v1['spur']) else str(v1['spur'][0])
    v5_s = f"{min(v5['spur'])}-{max(v5['spur'])}" if min(v5['spur']) != max(v5['spur']) else str(v5['spur'][0])
    v1_f = sum(v1["fail"])
    v5_f = sum(v5["fail"])
    
    v1_total_tok += sum(v1["tok"])
    v5_total_tok += sum(v5["tok"])
    v1_total_spur += sum(v1["spur"])
    v5_total_spur += sum(v5["spur"])
    v1_total_fail += v1_f
    v5_total_fail += v5_f
    v1_runs += len(v1["tok"])
    v5_runs += len(v5["tok"])
    
    tok_delta = v5_avg_tok - v1_avg_tok
    pct = tok_delta / max(v1_avg_tok, 1) * 100
    
    print(f"{turn_id:>6} | {v1_avg_tok:>7} {v1_ent:>6} {v1_s:>6} {v1_f:>5} | {v5_avg_tok:>7} {v5_ent:>6} {v5_s:>6} {v5_f:>5}  ({pct:+.0f}%)")

print("-" * 80)
v1_avg = v1_total_tok // max(v1_runs, 1)
v5_avg = v5_total_tok // max(v5_runs, 1)
pct = (v5_avg - v1_avg) / max(v1_avg, 1) * 100
print(f"{'AVG':>6} | {v1_avg:>7} {'':>6} {v1_total_spur:>6} {v1_total_fail:>5} | {v5_avg:>7} {'':>6} {v5_total_spur:>6} {v5_total_fail:>5}  ({pct:+.0f}%)")

print(f"\nV1 total runs: {v1_runs}, V5 total runs: {v5_runs}")
print(f"V1 total spurious: {v1_total_spur}, V5 total spurious: {v5_total_spur}")
print(f"V1 failures: {v1_total_fail}, V5 failures: {v5_total_fail}")
print(f"Average token change: {pct:+.1f}%")

# Consistency analysis
print("\n--- Consistency Analysis ---")
for turn_id in turns:
    v1 = results[(turn_id, "v1")]
    v5 = results[(turn_id, "v5")]
    v1_range = max(v1['ents']) - min(v1['ents'])
    v5_range = max(v5['ents']) - min(v5['ents'])
    v1_cv = "consistent" if v1_range <= 1 else f"variable({v1_range})"
    v5_cv = "consistent" if v5_range <= 1 else f"variable({v5_range})"
    print(f"  {turn_id}: V1={v1_cv:>15}  V5={v5_cv:>15}")
