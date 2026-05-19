import json, re, os, sys

base = r'framework-local/ab-test/qwen35-pipelined'
log_path = os.path.join(base, 'extraction-log.jsonl')

entries = [json.loads(l) for l in open(log_path)]
print(f"Total log entries: {len(entries)}")
print(f"Last turn: {entries[-1]['turn_id']}")

def turn_num(tid):
    m = re.match(r'turn-0*(\d+)', tid)
    return int(m.group(1)) if m else 0

batch = [e for e in entries if 101 <= turn_num(e['turn_id']) <= 125]
print(f"\n=== THROUGHPUT (Batch 101-125) ===")
print(f"Turns extracted: {len(batch)} of 25")
for e in batch:
    ms = e.get('elapsed_ms', 0)
    errs = [k.replace('_ok','') for k in ['discovery_ok','detail_ok','pc_ok','relationships_ok','events_ok','temporal_ok'] if not e.get(k, True)]
    status = 'FAIL:'+','.join(errs) if errs else 'OK'
    print(f"  {e['turn_id']}: {ms/1000:.1f}s {status}")
if batch:
    times = [e['elapsed_ms']/1000 for e in batch]
    print(f"  Avg: {sum(times)/len(times):.1f}s  Slowest: {max(times):.1f}s  Fastest: {min(times):.1f}s  Total: {sum(times):.1f}s")

# Entity counts
print(f"\n=== ENTITY COUNTS ===")
cats = {}
for ctype, key in [('characters','characters'),('locations','locations'),('items','items'),('factions','factions')]:
    path = os.path.join(base, 'catalogs', ctype + '.json')
    if os.path.exists(path):
        data = json.load(open(path))
        cats[ctype] = data[key]
        print(f"  {ctype}: {len(data[key])}")
    else:
        cats[ctype] = []
        print(f"  {ctype}: FILE NOT FOUND")
total = sum(len(v) for v in cats.values())
print(f"  Total: {total}")

# New entities in 101-125
print(f"\n=== NEW ENTITIES (first_seen 101-125) ===")
for ctype, ents in cats.items():
    new = [e for e in ents if 101 <= turn_num(e.get('first_seen_turn','')) <= 125]
    if new:
        print(f"  {ctype}:")
        for e in new:
            print(f"    {e['id']:35s} {e['name']}")

# Errors
print(f"\n=== ERRORS (any *_ok=false in 101-125) ===")
found = False
for e in batch:
    errs = []
    for k in ['discovery_ok','detail_ok','pc_ok','relationships_ok','events_ok','temporal_ok']:
        if k in e and not e[k]:
            err_key = k.replace('_ok','_error')
            errs.append(f"{k.replace('_ok','')}: {e.get(err_key,'?')}")
    if errs:
        found = True
        print(f"  {e['turn_id']}:")
        for err in errs:
            print(f"    {err}")
if not found:
    print("  None - all passes OK")

# Quality check - sorted names for dup spotting
print(f"\n=== QUALITY: Characters (sorted by name) ===")
for c in sorted(cats.get('characters',[]), key=lambda x: x['name'].lower()):
    print(f"  {c['id']:35s} {c['name']}")

print(f"\n=== QUALITY: Items (sorted by name) ===")
for i in sorted(cats.get('items',[]), key=lambda x: x['name'].lower()):
    print(f"  {i['id']:35s} {i['name']}")

print(f"\n=== QUALITY: Locations (sorted by name) ===")
for l in sorted(cats.get('locations',[]), key=lambda x: x['name'].lower()):
    print(f"  {l['id']:35s} {l['name']}")
