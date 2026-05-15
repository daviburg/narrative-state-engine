#!/usr/bin/env python3
"""B7 slowdown root cause analysis — read-only, no modifications."""
import json
import sys
from collections import Counter, defaultdict

LOG = r"c:\Users\david\narrative-state-engine\framework-local\ab-test\v2-full-optimized\extraction-log.jsonl"

entries = []
with open(LOG) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # skip malformed lines

print(f"Total log entries: {len(entries)}")
turn_ids = [e["turn_id"] for e in entries]
dupes = {k: v for k, v in Counter(turn_ids).items() if v > 1}
print(f"Unique turns: {len(set(turn_ids))}")
print(f"Re-extracted turns (>1 entry): {len(dupes)}")
print(f"Re-extraction wasted entries: {sum(v - 1 for v in dupes.values())}")

# Keep only LAST entry per turn (final successful extraction)
last_by_turn = {}
for e in entries:
    last_by_turn[e["turn_id"]] = e
final_entries = sorted(last_by_turn.values(), key=lambda e: e["turn_id"])

# ─── Per-phase breakdown helper ───
def phase_stats(subset, label):
    print(f"\n{'='*60}")
    print(f"  {label}  (n={len(subset)} turns)")
    print(f"{'='*60}")
    
    phases = ["discovery", "entity_detail", "relationship_mapper", "event_extractor", "pc_update", "temporal"]
    phase_totals = {p: {"tokens": 0, "calls": 0, "turns_with": 0} for p in phases}
    elapsed_list = []
    parallel_list = []
    discovery_ms_list = []
    
    for e in subset:
        elapsed_list.append(e.get("elapsed_ms", 0))
        parallel_list.append(e.get("parallel_ms", 0))
        discovery_ms_list.append(e.get("discovery_ms", 0))
        pm = e.get("prompt_metrics", {})
        for p in phases:
            if p in pm:
                phase_totals[p]["tokens"] += pm[p].get("input_tokens", 0)
                phase_totals[p]["calls"] += pm[p].get("calls", 0)
                phase_totals[p]["turns_with"] += 1

    total_tokens = sum(v["tokens"] for v in phase_totals.values())
    total_calls = sum(v["calls"] for v in phase_totals.values())
    n = len(subset)
    
    print(f"\n  Timing: elapsed avg={sum(elapsed_list)/n:.0f}ms, "
          f"parallel avg={sum(parallel_list)/n:.0f}ms, "
          f"discovery avg={sum(discovery_ms_list)/n:.0f}ms")
    print(f"  Total input tokens across {n} turns: {total_tokens:,}")
    print(f"  Avg input tokens/turn: {total_tokens/n:,.0f}")
    
    print(f"\n  {'Phase':<22} {'Tokens':>10} {'%':>6} {'Calls':>6} {'Avg/Call':>10} {'Turns':>6}")
    print(f"  {'-'*22} {'-'*10} {'-'*6} {'-'*6} {'-'*10} {'-'*6}")
    for p in phases:
        v = phase_totals[p]
        pct = (v["tokens"] / total_tokens * 100) if total_tokens else 0
        avg_per_call = v["tokens"] / v["calls"] if v["calls"] else 0
        print(f"  {p:<22} {v['tokens']:>10,} {pct:>5.1f}% {v['calls']:>6} {avg_per_call:>10,.0f} {v['turns_with']:>6}")
    
    print(f"  {'TOTAL':<22} {total_tokens:>10,} {'100%':>6} {total_calls:>6}")
    
    # Per-turn detail for entity_detail
    print(f"\n  Entity detail per-turn breakdown:")
    ed_per_turn = []
    for e in subset:
        pm = e.get("prompt_metrics", {})
        ed = pm.get("entity_detail", {})
        ed_per_turn.append({
            "turn": e["turn_id"],
            "tokens": ed.get("input_tokens", 0),
            "calls": ed.get("calls", 0),
            "elapsed": e.get("elapsed_ms", 0),
            "parallel": e.get("parallel_ms", 0),
        })
    
    for t in ed_per_turn:
        avg = t["tokens"] // t["calls"] if t["calls"] else 0
        print(f"    {t['turn']}: ed_tokens={t['tokens']:>6,} ({t['calls']} calls, avg={avg:,}), "
              f"elapsed={t['elapsed']:>7,}ms, parallel={t['parallel']:>7,}ms")

# Define ranges
def in_range(turn_id, lo, hi):
    num = int(turn_id.split("-")[1])
    return lo <= num <= hi

early = [e for e in final_entries if in_range(e["turn_id"], 1, 10)]
mid = [e for e in final_entries if in_range(e["turn_id"], 150, 160)]
late1 = [e for e in final_entries if in_range(e["turn_id"], 300, 310)]
late2 = [e for e in final_entries if in_range(e["turn_id"], 330, 344)]
batch7 = [e for e in final_entries if in_range(e["turn_id"], 301, 344)]

phase_stats(early, "EARLY (turns 1-10)")
phase_stats(mid, "MID (turns 150-160)")
phase_stats(late1, "LATE-A (turns 300-310)")
phase_stats(late2, "LATE-B (turns 330-344)")

# ─── Section B: Token Growth Curve ───
print(f"\n{'='*60}")
print(f"  TOKEN GROWTH: entity_detail input_tokens/call over time")
print(f"{'='*60}")

# Group by 25-turn buckets
buckets = defaultdict(list)
for e in final_entries:
    num = int(e["turn_id"].split("-")[1])
    bucket = (num - 1) // 25 * 25 + 1
    pm = e.get("prompt_metrics", {})
    ed = pm.get("entity_detail", {})
    calls = ed.get("calls", 0)
    tokens = ed.get("input_tokens", 0)
    if calls > 0:
        buckets[bucket].append(tokens / calls)

print(f"\n  {'Bucket':>10} {'Turns':>6} {'Avg Tok/Call':>14} {'Min':>8} {'Max':>8}")
for b in sorted(buckets.keys()):
    vals = buckets[b]
    print(f"  {b:>4}-{b+24:>4} {len(vals):>6} {sum(vals)/len(vals):>14,.0f} "
          f"{min(vals):>8,.0f} {max(vals):>8,.0f}")

# ─── Section C: Theoretical Time Budget ───
print(f"\n{'='*60}")
print(f"  THEORETICAL TIME BUDGET")
print(f"{'='*60}")

TOK_PER_SEC = 52
WORKERS = 2

for label, subset in [("EARLY avg", early), ("MID avg", mid), ("LATE-B avg", late2), ("B7 all", batch7)]:
    total_input = 0
    total_calls = 0
    total_elapsed = 0
    for e in subset:
        total_elapsed += e.get("elapsed_ms", 0)
        pm = e.get("prompt_metrics", {})
        for p in pm.values():
            total_input += p.get("input_tokens", 0)
            total_calls += p.get("calls", 0)
    n = len(subset)
    avg_input = total_input / n
    avg_calls = total_calls / n
    avg_elapsed = total_elapsed / n
    
    # Theoretical: all calls run with 2 workers
    # Each call: prompt processing (input_tokens varies, but generation is the bottleneck)
    # For OpenVINO, input processing (prefill) is much faster than generation
    # Output tokens ~200-500 per call. Assume 300 avg.
    avg_output_per_call = 300
    serial_rounds = (avg_calls + WORKERS - 1) // WORKERS
    time_per_round = avg_output_per_call / TOK_PER_SEC  # generation time in seconds
    theoretical_s = serial_rounds * time_per_round
    
    print(f"\n  {label} (n={n}):")
    print(f"    Avg input tokens/turn: {avg_input:,.0f}")
    print(f"    Avg calls/turn: {avg_calls:.1f}")
    print(f"    Avg elapsed: {avg_elapsed/1000:.1f}s")
    print(f"    Serial rounds (ceil(calls/{WORKERS})): {serial_rounds:.0f}")
    print(f"    Theoretical generation time ({avg_output_per_call} output tok × {serial_rounds:.0f} rounds / {TOK_PER_SEC} tok/s): {theoretical_s:.1f}s")
    print(f"    Overhead ratio: {(avg_elapsed/1000) / theoretical_s:.1f}x theoretical" if theoretical_s > 0 else "    N/A")

# ─── Section D: Dominant Cost Driver ───
print(f"\n{'='*60}")
print(f"  DOMINANT COST DRIVER (late game)")
print(f"{'='*60}")

# For batch 7, rank phases by total input tokens
phases = ["discovery", "entity_detail", "relationship_mapper", "event_extractor", "pc_update", "temporal"]
phase_agg = {p: 0 for p in phases}
for e in batch7:
    pm = e.get("prompt_metrics", {})
    for p in phases:
        if p in pm:
            phase_agg[p] += pm[p].get("input_tokens", 0)

total = sum(phase_agg.values())
print(f"\n  B7 total input tokens: {total:,}")
for p in sorted(phase_agg, key=lambda x: phase_agg[x], reverse=True):
    v = phase_agg[p]
    print(f"    {p:<22} {v:>10,} ({v/total*100:.1f}%)")

# Outliers in batch 7
print(f"\n  Top 10 highest-token turns in B7:")
b7_sorted = sorted(batch7, key=lambda e: sum(p.get("input_tokens", 0) for p in e.get("prompt_metrics", {}).values()), reverse=True)
for e in b7_sorted[:10]:
    pm = e.get("prompt_metrics", {})
    total_t = sum(p.get("input_tokens", 0) for p in pm.values())
    ed = pm.get("entity_detail", {})
    print(f"    {e['turn_id']}: total={total_t:,}, ed={ed.get('input_tokens',0):,}({ed.get('calls',0)} calls), elapsed={e['elapsed_ms']:,}ms")

# ─── Section E: Re-extraction Overhead ───
print(f"\n{'='*60}")
print(f"  RE-EXTRACTION OVERHEAD")
print(f"{'='*60}")

# Identify all non-last entries (wasted)
seen = set()
wasted_entries = []
for e in reversed(entries):
    tid = e["turn_id"]
    if tid in seen:
        wasted_entries.append(e)
    seen.add(tid)

wasted_ms = sum(e.get("elapsed_ms", 0) for e in wasted_entries)
total_ms = sum(e.get("elapsed_ms", 0) for e in entries)
print(f"\n  Total extraction time: {total_ms/1000/3600:.2f}h ({total_ms/1000:.0f}s)")
print(f"  Wasted on re-extraction: {wasted_ms/1000/3600:.2f}h ({wasted_ms/1000:.0f}s)")
print(f"  Re-extraction overhead: {wasted_ms/total_ms*100:.1f}%")
print(f"  Wasted entries: {len(wasted_entries)}")

# Which turns were re-extracted?
dupe_turns = sorted(dupes.keys())
print(f"\n  Re-extracted turns: {', '.join(dupe_turns[:20])}{'...' if len(dupe_turns)>20 else ''}")

# ─── Section F: Parallelism Efficiency ───
print(f"\n{'='*60}")
print(f"  PARALLELISM EFFICIENCY (B7)")
print(f"{'='*60}")

for e in batch7[-10:]:
    pm = e.get("prompt_metrics", {})
    ed = pm.get("entity_detail", {})
    ed_calls = ed.get("calls", 0)
    ed_tokens = ed.get("input_tokens", 0)
    
    # Count total calls in parallel phase (everything except discovery)
    parallel_calls = 0
    parallel_tokens = 0
    for pname, pdata in pm.items():
        if pname != "discovery":
            parallel_calls += pdata.get("calls", 0)
            parallel_tokens += pdata.get("input_tokens", 0)
    
    parallel_ms = e.get("parallel_ms", 0)
    elapsed_ms = e.get("elapsed_ms", 0)
    discovery_ms = e.get("discovery_ms", 0)
    
    # Theoretical: parallel_calls/2 rounds, ~300 output tokens each at 52 tok/s
    serial_rounds = (parallel_calls + 1) // 2
    theoretical_gen = serial_rounds * 300 / TOK_PER_SEC
    
    print(f"  {e['turn_id']}: parallel_calls={parallel_calls}, parallel_ms={parallel_ms:,}, "
          f"theoretical_gen={theoretical_gen:.0f}s, ratio={parallel_ms/1000/theoretical_gen:.1f}x" if theoretical_gen > 0 else
          f"  {e['turn_id']}: parallel_calls={parallel_calls}, parallel_ms={parallel_ms:,}")
