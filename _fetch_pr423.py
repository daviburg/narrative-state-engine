"""Fetch unreplied review comments on PR #423."""
import subprocess
import json

r = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/423/comments', '--paginate'],
    capture_output=True
)
if r.returncode != 0:
    print(f"Error: {r.stderr.decode('utf-8', errors='replace')}")
    exit(1)

data = json.loads(r.stdout.decode('utf-8'))
top = [c for c in data if not c.get('in_reply_to_id')]
replies = [c for c in data if c.get('in_reply_to_id')]
replied_ids = set(c['in_reply_to_id'] for c in replies)
unreplied = [c for c in top if c['id'] not in replied_ids]

print(f"Total comments: {len(data)}")
print(f"Top-level: {len(top)}")
print(f"Replies: {len(replies)}")
print(f"=== PR #423 unreplied: {len(unreplied)} ===")
print()

for c in unreplied:
    print("---")
    print(f"ID: {c['id']}")
    print(f"Path: {c['path']}")
    print(f"Line: {c.get('line')}")
    print(f"Original line: {c.get('original_line')}")
    print(f"Side: {c.get('side')}")
    print(f"Author: {c.get('user', {}).get('login')}")
    print(f"Created: {c.get('created_at')}")
    print(f"Body:\n{c['body']}")
    print()
