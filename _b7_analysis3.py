import json
entries = []
with open(r"framework-local/ab-test/v2-full-optimized/extraction-log.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except:
            pass

t332 = [e for e in entries if e["turn_id"] == "turn-332"]
print(f"turn-332 entries: {len(t332)}")
for i, e in enumerate(t332):
    pm = e.get("prompt_metrics", {})
    ts = e.get("timestamp", "")
    print(f"  Entry {i}: timestamp={ts}")
    for phase, data in pm.items():
        print(f"    {phase}: {data}")
    print(f"    elapsed={e.get('elapsed_ms', 0)}ms")

last = {}
for e in entries:
    last[e["turn_id"]] = e
final = sorted(last.values(), key=lambda e: e["turn_id"])

over7 = [
    e for e in final
    if e.get("prompt_metrics", {}).get("entity_detail", {}).get("calls", 0) > 7
]
print(f"\nTurns with >7 entity_detail calls: {len(over7)}/{len(final)}")
for e in over7[:10]:
    ed = e.get("prompt_metrics", {}).get("entity_detail", {})
    tid = e["turn_id"]
    print(f"  {tid}: {ed.get('calls', 0)} calls, {ed.get('input_tokens', 0):,} tokens")
