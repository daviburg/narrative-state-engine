import json, subprocess, sys

result = subprocess.run(
    ["gh", "api", "repos/daviburg/narrative-state-engine/pulls/94/comments"],
    capture_output=True, text=True, timeout=30
)
if result.returncode != 0:
    print("ERROR:", result.stderr, file=sys.stderr)
    sys.exit(1)

cs = json.loads(result.stdout)

for c in cs:
    body_first_line = c["body"].split("\n")[0][:100]
    reply = c.get("in_reply_to_id") or "root"
    print(f"ID: {c['id']} | User: {c['user']['login']} | Reply-to: {reply}")
    print(f"  File: {c.get('path', 'N/A')}")
    print(f"  Body: {body_first_line}")
    print("---")
