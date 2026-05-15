import json, re
log_path = r'c:\Users\david\narrative-state-engine\framework-local\ab-test\qwen35-pipelined\extraction-log.jsonl'
entries = []
with open(log_path) as f:
    for line in f:
        if line.strip():
            entries.append(json.loads(line))

print(f'Total log entries: {len(entries)}')
for e in entries[-5:]:
    turn = e.get('turn_id', 'unknown')
    t_ms = e.get('elapsed_ms', 0)
    print(f"  {turn}: {t_ms/1000.0:.1f}s, ok={e.get('discovery_ok','?')}/{e.get('detail_ok','?')}/{e.get('relationships_ok','?')}/{e.get('events_ok','?')}")

batch = [e for e in entries if 'turn_id' in e and 101 <= int(re.sub(r'turn-0*(\d+)', r'\1', e['turn_id'])) <= 125]
print(f'Batch 101-125: {len(batch)} turns found')
if batch:
    times = [e.get('elapsed_ms', 0)/1000.0 for e in batch]
    print(f'  Avg: {sum(times)/len(times):.1f}s/turn')
    print(f'  Slowest: {max(times):.1f}s')
    print(f'  Fastest: {min(times):.1f}s')
    print(f'  Total: {sum(times):.1f}s')
