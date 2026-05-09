"""Analyze comparison results for any platform."""
import json, sys
from collections import defaultdict

if len(sys.argv) < 2:
    print("Usage: python analyze_comparison.py <json-file> [json-file2 ...]")
    sys.exit(1)

for filepath in sys.argv[1:]:
    with open(filepath) as f:
        data = json.load(f)

    platform = data[0].get("platform", filepath) if data else filepath
    print(f"\n{'='*80}")
    print(f"  Platform: {platform}")
    print(f"  Data points: {len(data)}")
    print(f"{'='*80}")

    results = defaultdict(lambda: defaultdict(list))
    for entry in data:
        key = (entry["turn"], entry["template"])
        results[key]["ents"].append(entry.get("entity_count", 0))
        results[key]["tok"].append(entry.get("output_tokens_est", 0))
        results[key]["spur"].append(entry.get("spurious", 0))
        results[key]["compact"].append(entry.get("compact_count", 0))
        results[key]["fail"].append(not entry.get("success", True))
        results[key]["time"].append(entry.get("time_s", 0))

    print(f"\n{'Turn':>6} | {'--- V1 (original) ---':^35} | {'--- V5 (optimized) ---':^35}")
    print(f"{'':>6} | {'AvgTok':>7} {'Ent':>6} {'Spur':>6} {'Time':>6} {'Fail':>4} | {'AvgTok':>7} {'Ent':>6} {'Spur':>6} {'Time':>6} {'Fail':>4} | {'Δ%':>5}")
    print("-" * 100)

    turns = sorted(set(e["turn"] for e in data))
    totals = {"v1_tok": 0, "v5_tok": 0, "v1_spur": 0, "v5_spur": 0, 
              "v1_fail": 0, "v5_fail": 0, "v1_n": 0, "v5_n": 0,
              "v1_time": 0, "v5_time": 0}

    for turn_id in turns:
        v1 = results[(turn_id, "v1")]
        v5 = results[(turn_id, "v5")]
        
        def fmt_range(vals):
            if not vals: return "—"
            mn, mx = min(vals), max(vals)
            return str(mn) if mn == mx else f"{mn}-{mx}"
        
        v1_avg_tok = sum(v1["tok"]) // max(len(v1["tok"]), 1) if v1["tok"] else 0
        v5_avg_tok = sum(v5["tok"]) // max(len(v5["tok"]), 1) if v5["tok"] else 0
        v1_avg_t = sum(v1["time"]) / max(len(v1["time"]), 1) if v1["time"] else 0
        v5_avg_t = sum(v5["time"]) / max(len(v5["time"]), 1) if v5["time"] else 0
        v1_f = sum(v1["fail"])
        v5_f = sum(v5["fail"])
        
        totals["v1_tok"] += sum(v1["tok"]); totals["v5_tok"] += sum(v5["tok"])
        totals["v1_spur"] += sum(v1["spur"]); totals["v5_spur"] += sum(v5["spur"])
        totals["v1_fail"] += v1_f; totals["v5_fail"] += v5_f
        totals["v1_n"] += len(v1["tok"]); totals["v5_n"] += len(v5["tok"])
        totals["v1_time"] += sum(v1["time"]); totals["v5_time"] += sum(v5["time"])
        
        pct = (v5_avg_tok - v1_avg_tok) / max(v1_avg_tok, 1) * 100 if v1_avg_tok else 0
        
        print(f"{turn_id:>6} | {v1_avg_tok:>7} {fmt_range(v1['ents']):>6} {fmt_range(v1['spur']):>6} {v1_avg_t:>5.1f}s {v1_f:>4} | {v5_avg_tok:>7} {fmt_range(v5['ents']):>6} {fmt_range(v5['spur']):>6} {v5_avg_t:>5.1f}s {v5_f:>4} | {pct:>+5.0f}%")

    print("-" * 100)
    v1a = totals["v1_tok"] // max(totals["v1_n"], 1)
    v5a = totals["v5_tok"] // max(totals["v5_n"], 1)
    v1t = totals["v1_time"] / max(totals["v1_n"], 1)
    v5t = totals["v5_time"] / max(totals["v5_n"], 1)
    pct = (v5a - v1a) / max(v1a, 1) * 100
    print(f"{'AVG':>6} | {v1a:>7} {'':>6} {totals['v1_spur']:>6} {v1t:>5.1f}s {totals['v1_fail']:>4} | {v5a:>7} {'':>6} {totals['v5_spur']:>6} {v5t:>5.1f}s {totals['v5_fail']:>4} | {pct:>+5.0f}%")
    
    # Consistency
    print(f"\n--- Consistency (entity count range across 3 runs) ---")
    for turn_id in turns:
        v1 = results[(turn_id, "v1")]
        v5 = results[(turn_id, "v5")]
        v1r = max(v1['ents']) - min(v1['ents']) if v1['ents'] else 0
        v5r = max(v5['ents']) - min(v5['ents']) if v5['ents'] else 0
        v1c = "consistent" if v1r <= 1 else f"variable(±{v1r})"
        v5c = "consistent" if v5r <= 1 else f"variable(±{v5r})"
        print(f"  {turn_id}: V1={v1c:>15}  V5={v5c:>15}")
