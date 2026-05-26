import subprocess, json

r = subprocess.run(
    ['C:/Program Files/GitHub CLI/gh.exe', 'api',
     'repos/daviburg/narrative-state-engine/commits/b525247af47e184c66ee530d6fa2e761c3f7221e/check-runs'],
    capture_output=True, text=True
)
if r.returncode != 0:
    print(f"Error: {r.stderr}")
else:
    d = json.loads(r.stdout)
    for c in d.get('check_runs', []):
        print(f"{c['name']}: {c['status']}/{c.get('conclusion', 'pending')}")
    if not d.get('check_runs'):
        print("No check runs found yet")
