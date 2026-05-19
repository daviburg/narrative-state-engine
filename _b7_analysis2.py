#!/usr/bin/env python3
"""Per-call timing and growth analysis."""
import json

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
            pass

# Keep last per turn
last = {}
for e in entries:
    last[e["turn_id"]] = e
final = sorted(last.values(), key=lambda e: e["turn_id"])

# ─── Per-call timing for Late-B ───
print("=== PER-CALL TIMING (Late-B: turns 330-344) ===")
header = f"{'Turn':<10} {'ParMS':>8} {'EDCalls':>7} {'EDTok':>8} {'AvgTok':>8} {'TotCalls':>9} {'SerRnds':>8} {'ms/Round':>9} {'ms/Call':>9}"
print(header)
for e in final:
    num = int(e["turn_id"].split("-")[1])
    if 330 <= num <= 344:
        pm = e.get("prompt_metrics", {})
        ed = pm.get("entity_detail", {})
        ed_calls = ed.get("calls", 0)
        ed_tok = ed.get("input_tokens", 0)
        avg_tok = ed_tok // ed_calls if ed_calls else 0

        total_calls = sum(
            p.get("calls", 0) for pname, p in pm.items() if pname != "discovery"
        )
        serial_rounds = (total_calls + 1) // 2
        parallel_ms = e.get("parallel_ms", 0)
        time_per_round = parallel_ms / serial_rounds if serial_rounds else 0
        est_per_call = parallel_ms / total_calls if total_calls else 0

        print(
            f"{e['turn_id']:<10} {parallel_ms:>8} {ed_calls:>7} {ed_tok:>8}"
            f" {avg_tok:>8} {total_calls:>9} {serial_rounds:>8}"
            f" {time_per_round:>8.0f}  {est_per_call:>8.0f}"
        )

# ─── Discovery prompt growth ───
print("\n=== DISCOVERY PROMPT GROWTH ===")
for bucket_start in range(1, 350, 50):
    bucket_end = bucket_start + 49
    subset = [
        e
        for e in final
        if bucket_start <= int(e["turn_id"].split("-")[1]) <= bucket_end
    ]
    if not subset:
        continue
    disc_tokens = [
        e.get("prompt_metrics", {}).get("discovery", {}).get("input_tokens", 0)
        for e in subset
    ]
    avg_disc = sum(disc_tokens) / len(disc_tokens) if disc_tokens else 0
    max_disc = max(disc_tokens) if disc_tokens else 0
    print(f"  Turns {bucket_start:>3}-{bucket_end:>3}: disc avg={avg_disc:>8,.0f}  max={max_disc:>8,}")

# ─── Relationship mapper growth ───
print("\n=== RELATIONSHIP MAPPER GROWTH ===")
for bucket_start in range(1, 350, 50):
    bucket_end = bucket_start + 49
    subset = [
        e
        for e in final
        if bucket_start <= int(e["turn_id"].split("-")[1]) <= bucket_end
    ]
    if not subset:
        continue
    rel_entries = [
        e.get("prompt_metrics", {}).get("relationship_mapper", {})
        for e in subset
    ]
    rel_entries = [r for r in rel_entries if r]
    if rel_entries:
        avg_rel = sum(r.get("input_tokens", 0) for r in rel_entries) / len(rel_entries)
        max_rel = max(r.get("input_tokens", 0) for r in rel_entries)
        count = len(rel_entries)
        print(
            f"  Turns {bucket_start:>3}-{bucket_end:>3}:"
            f" rel avg={avg_rel:>8,.0f}  max={max_rel:>8,}  (n={count})"
        )

# ─── Event extractor growth ───
print("\n=== EVENT EXTRACTOR GROWTH ===")
for bucket_start in range(1, 350, 50):
    bucket_end = bucket_start + 49
    subset = [
        e
        for e in final
        if bucket_start <= int(e["turn_id"].split("-")[1]) <= bucket_end
    ]
    if not subset:
        continue
    evt_entries = [
        e.get("prompt_metrics", {}).get("event_extractor", {})
        for e in subset
    ]
    evt_entries = [r for r in evt_entries if r]
    if evt_entries:
        avg_evt = sum(r.get("input_tokens", 0) for r in evt_entries) / len(evt_entries)
        print(f"  Turns {bucket_start:>3}-{bucket_end:>3}: evt avg={avg_evt:>8,.0f}  (n={len(evt_entries)})")

# ─── Outlier analysis: turns > 400s ───
print("\n=== OUTLIER TURNS (elapsed > 400s) ===")
outliers = [e for e in final if e.get("elapsed_ms", 0) > 400000]
outliers.sort(key=lambda e: e["elapsed_ms"], reverse=True)
for e in outliers[:15]:
    pm = e.get("prompt_metrics", {})
    ed = pm.get("entity_detail", {})
    disc = pm.get("discovery", {})
    rel = pm.get("relationship_mapper", {})
    total_input = sum(p.get("input_tokens", 0) for p in pm.values())
    total_calls = sum(p.get("calls", 0) for p in pm.values())
    print(
        f"  {e['turn_id']}: {e['elapsed_ms']/1000:.0f}s"
        f"  total_input={total_input:,}"
        f"  total_calls={total_calls}"
        f"  ed={ed.get('input_tokens', 0):,}({ed.get('calls', 0)})"
        f"  disc={disc.get('input_tokens', 0):,}"
        f"  rel={rel.get('input_tokens', 0):,}"
    )

# ─── Re-extraction analysis ───
print("\n=== RE-EXTRACTION DETAIL ===")
from collections import Counter

turn_counts = Counter(e["turn_id"] for e in entries)
multi = {k: v for k, v in turn_counts.items() if v > 1}
print(f"  Turns extracted >1 time: {len(multi)}")
# Group contiguous ranges
multi_turns = sorted(multi.keys())
if multi_turns:
    ranges = []
    start = multi_turns[0]
    prev = start
    for t in multi_turns[1:]:
        prev_num = int(prev.split("-")[1])
        t_num = int(t.split("-")[1])
        if t_num == prev_num + 1:
            prev = t
        else:
            ranges.append((start, prev))
            start = t
            prev = t
    ranges.append((start, prev))
    print("  Contiguous ranges of re-extracted turns:")
    for s, e_turn in ranges:
        s_num = int(s.split("-")[1])
        e_num = int(e_turn.split("-")[1])
        count = e_num - s_num + 1
        print(f"    {s} to {e_turn} ({count} turns)")

# ─── Prefill estimation ───
print("\n=== PREFILL TIME ESTIMATION ===")
# For turns 330-344, estimate prefill vs generation
for e in final:
    num = int(e["turn_id"].split("-")[1])
    if num not in (335, 340, 344):
        continue
    pm = e.get("prompt_metrics", {})
    parallel_ms = e.get("parallel_ms", 0)
    
    # Sum all non-discovery calls
    total_input = sum(p.get("input_tokens", 0) for pname, p in pm.items() if pname != "discovery")
    total_calls = sum(p.get("calls", 0) for pname, p in pm.items() if pname != "discovery")
    
    # Assume 300 output tokens per call at 52 tok/s
    gen_time = total_calls * 300 / 52  # seconds, but only half due to 2 workers
    gen_time_parallel = gen_time / 2 * 1000  # ms, with 2 workers
    
    prefill_residual = parallel_ms - gen_time_parallel
    implied_prefill_rate = total_input / (prefill_residual / 1000) if prefill_residual > 0 else 0
    
    print(f"  {e['turn_id']}:")
    print(f"    parallel_ms={parallel_ms:,}")
    print(f"    total_input_tokens={total_input:,} across {total_calls} calls")
    print(f"    est generation time (2 workers, 300 out tok): {gen_time_parallel:,.0f}ms")
    print(f"    residual (prefill+overhead): {prefill_residual:,.0f}ms")
    print(f"    implied prefill rate: {implied_prefill_rate:,.0f} tok/s")
