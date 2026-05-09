"""Run discovery_baseline.py for each turn in a separate process to work around B70 keepalive bug."""
import subprocess, json, sys, os

TURNS = [int(t) for t in sys.argv[1].split(",")] if len(sys.argv) > 1 else [201, 211, 221, 251, 300, 306, 312, 340]
TEMPLATE = sys.argv[2] if len(sys.argv) > 2 else "templates/extraction/entity-discovery-v2.md"
CONFIG = sys.argv[3] if len(sys.argv) > 3 else "config/llm.json"
OUTPUT = sys.argv[4] if len(sys.argv) > 4 else "test-data/v2-b70.json"

all_results = []

for t in TURNS:
    out_file = f"test-data/_b70_turn{t}.json"
    cmd = [
        sys.executable, "-u", "tools/discovery_baseline.py",
        "--turns", str(t),
        "--template", TEMPLATE,
        "--config", CONFIG,
        "--output-json", out_file,
    ]
    print(f"\n=== Turn {t} ===", flush=True)
    r = subprocess.run(cmd, capture_output=False, text=True)
    if r.returncode != 0:
        print(f"  FAILED (exit {r.returncode})")
        continue
    try:
        with open(out_file, "r") as f:
            data = json.load(f)
        all_results.extend(data)
    except Exception as e:
        print(f"  Error reading {out_file}: {e}")

with open(OUTPUT, "w") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print(f"\nAll results written to {OUTPUT}")
