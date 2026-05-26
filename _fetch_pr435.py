import subprocess, json, sys

# Fetch PR info
r = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435'],
    capture_output=True, text=True,
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r.returncode != 0:
    print(f"ERROR fetching PR: {r.stderr}", file=sys.stderr)
    sys.exit(1)

pr = json.loads(r.stdout)
print(f"PR #435: {pr['title']}")
print(f"State: {pr['state']}")
print(f"Author: {pr['user']['login']}")
print()

# Fetch comments
r2 = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435/comments'],
    capture_output=True, text=True,
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r2.returncode != 0:
    print(f"ERROR fetching comments: {r2.stderr}", file=sys.stderr)
    sys.exit(1)

comments = json.loads(r2.stdout)
print(f"Total review comments: {len(comments)}")
print()

# Find top-level comments (no in_reply_to_id) and check which have replies
top_level = [c for c in comments if c.get('in_reply_to_id') is None]
replies = [c for c in comments if c.get('in_reply_to_id') is not None]
replied_ids = {c['in_reply_to_id'] for c in replies}

unresolved = [c for c in top_level if c['id'] not in replied_ids]

print(f"Top-level comments: {len(top_level)}")
print(f"Comments with replies: {len(top_level) - len(unresolved)}")
print(f"UNRESOLVED (no reply): {len(unresolved)}")
print()

for c in unresolved:
    print("=" * 70)
    print(f"Comment ID: {c['id']}")
    print(f"Author: {c['user']['login']}")
    print(f"File: {c['path']}")
    print(f"Line: {c.get('line') or c.get('original_line')}")
    print(f"Created: {c['created_at']}")
    print(f"Body:\n{c['body']}")
    print()

# Also fetch reviews
r3 = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435/reviews'],
    capture_output=True, text=True,
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r3.returncode == 0:
    reviews = json.loads(r3.stdout)
    print("=" * 70)
    print(f"\nRecent reviews (last 3):")
    for rev in reviews[-3:]:
        print(f"  {rev['user']['login']} - {rev['state']} - {rev['submitted_at']}")
