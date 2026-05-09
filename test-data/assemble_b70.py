import json, glob

data = []
for f in sorted(glob.glob("test-data/_b70_turn*.json")):
    data.extend(json.load(open(f)))

with open("test-data/v2-b70.json", "w") as out:
    json.dump(data, out, indent=2, ensure_ascii=False)

print(f"Assembled {len(data)} turn results")
for d in data:
    c = d.get("compact_count", 0)
    print(f"  Turn {d['turn']}: {d['entity_count']} entities, compact={c}, "
          f"{d['output_tokens']} tok, {d['elapsed_seconds']:.1f}s, "
          f"active={d['active_count']}, passive={d['passive_count']}, "
          f"spurious={d['spurious_count']}")
