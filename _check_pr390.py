import json, sys, subprocess, os

os.environ["PYTHONIOENCODING"] = "utf-8"
repo = "daviburg/narrative-state-engine"
pr = 390

# Inline review comments
r = subprocess.run(["gh", "api", f"repos/{repo}/pulls/{pr}/comments"], capture_output=True, encoding="utf-8")
comments = json.loads(r.stdout)
print(f"=== PR #{pr} Inline Review Comments ({len(comments)}) ===")
for c in comments:
    reply_to = c.get("in_reply_to_id", "none")
    print(f"ID: {c['id']}")
    print(f"Author: {c['user']['login']}")
    print(f"File: {c['path']} Line: {c.get('line', 'N/A')}")
    print(f"Reply_to: {reply_to}")
    print(f"Body: {c['body'][:600]}")
    print("---")

# Find which top-level comments have replies
top_ids = {c['id'] for c in comments if c.get('in_reply_to_id') is None}
replied_ids = {c['in_reply_to_id'] for c in comments if c.get('in_reply_to_id') is not None}
unreplied = top_ids - replied_ids
if unreplied:
    print(f"\n*** UNREPLIED top-level comment IDs: {unreplied}")
else:
    print("\nAll top-level inline comments have at least one reply.")

# Issue-level comments
r2 = subprocess.run(["gh", "api", f"repos/{repo}/issues/{pr}/comments"], capture_output=True, encoding="utf-8")
issue_comments = json.loads(r2.stdout)
print(f"\n=== PR #{pr} Issue Comments ({len(issue_comments)}) ===")
for c in issue_comments:
    print(f"Author: {c['user']['login']}")
    print(f"Body: {c['body'][:600]}")
    print("---")

# Reviews
r3 = subprocess.run(["gh", "api", f"repos/{repo}/pulls/{pr}/reviews"], capture_output=True, encoding="utf-8")
reviews = json.loads(r3.stdout)
print(f"\n=== PR #{pr} Reviews ({len(reviews)}) ===")
for rv in reviews:
    print(f"Author: {rv['user']['login']} State: {rv['state']}")
    print(f"Body: {rv.get('body','')[:400]}")
    print("---")
