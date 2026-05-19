import json, os, re
base = "framework-local/ab-test/qwen35-pipelined/catalogs"
for cat in ["characters","locations","items","factions"]:
    d = os.path.join(base, cat)
    if not os.path.isdir(d): continue
    print(f"=== ALL {cat.upper()} ===")
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"): continue
        data = json.load(open(os.path.join(d, f)))
        if isinstance(data, list): data = data[0] if data else {}
        eid = data.get("id","?")
        ename = data.get("name","?")
        fst = data.get("first_seen_turn","?")
        print(f"  {eid:<35} {ename:<35} {fst}")
    print()

# Events and relationships
ev = json.load(open(os.path.join(base,"events.json")))
events = ev.get("events", ev) if isinstance(ev, dict) else ev
print(f"Events: {len(events) if isinstance(events, list) else 'unknown'}")
ri = json.load(open(os.path.join(base,"relationship-index.json")))
rels = ri.get("relationships", ri) if isinstance(ri, dict) else ri
print(f"Relationships: {len(rels) if isinstance(rels, list) else 'unknown'}")
tl = json.load(open(os.path.join(base,"timeline.json")))
if isinstance(tl, dict):
    sigs = tl.get("signals", tl.get("temporal_signals", []))
    print(f"Temporal signals: {len(sigs)}")

# Throughput
lines = open(os.path.join(base, "..", "extraction-log.jsonl")).readlines()
log = [json.loads(l) for l in lines]
def tn(t):
    m = re.search(r"turn-0*(\d+)", t)
    return int(m.group(1)) if m else 0
b1 = [e for e in log if tn(e["turn_id"]) <= 25]
b3 = [e for e in log if 51 <= tn(e["turn_id"]) <= 75]
print(f"\nTotal log entries: {len(log)}, Unique turns: {len(set(e['turn_id'] for e in log))}")
if b1:
    avg1 = sum(e["elapsed_ms"] for e in b1)/len(b1)/1000
    print(f"Batch 1 (1-25): {len(b1)} entries, avg {avg1:.1f}s/turn")
if b3:
    avg3 = sum(e["elapsed_ms"] for e in b3)/len(b3)/1000
    mn3 = min(e["elapsed_ms"] for e in b3)/1000
    mx3 = max(e["elapsed_ms"] for e in b3)/1000
    print(f"Batch 3 (51-75): {len(b3)} entries, avg {avg3:.1f}s, min {mn3:.1f}s, max {mx3:.1f}s")
    if b1:
        print(f"Degradation from batch 1: {(avg3-avg1)/avg1*100:.1f}%")

# Errors
errors = []
for e in b3:
    for k in ["discovery_error","detail_error","pc_error","relationships_error","events_error","temporal_error"]:
        if e.get(k): errors.append(f"{e['turn_id']}: {k}={e[k]}")
    for k in ["discovery_ok","detail_ok","events_ok","temporal_ok"]:
        if e.get(k) == False: errors.append(f"{e['turn_id']}: {k}=False")
if errors:
    print(f"\nErrors ({len(errors)}):")
    for err in errors: print(f"  {err}")
else:
    print("\nNo errors in batch 3")
