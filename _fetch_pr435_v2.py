import subprocess, json, sys, os

outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_pr435_result.txt')

lines = []

# Fetch PR info
r = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435'],
    capture_output=True, text=True, encoding='utf-8',
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r.returncode != 0:
    lines.append(f"ERROR fetching PR: {r.stderr}")
    with open(outpath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    sys.exit(1)

pr = json.loads(r.stdout)
lines.append(f"PR #435: {pr['title']}")
lines.append(f"State: {pr['state']}")
lines.append(f"Author: {pr['user']['login']}")
lines.append("")

# Fetch comments
r2 = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435/comments', '--paginate'],
    capture_output=True, text=True, encoding='utf-8',
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r2.returncode != 0:
    lines.append(f"ERROR fetching comments: {r2.stderr}")
    with open(outpath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    sys.exit(1)

comments = json.loads(r2.stdout)
lines.append(f"Total review comments: {len(comments)}")
lines.append("")

# Find top-level comments (no in_reply_to_id) and check which have replies
top_level = [c for c in comments if c.get('in_reply_to_id') is None]
replies = [c for c in comments if c.get('in_reply_to_id') is not None]
replied_ids = {c['in_reply_to_id'] for c in replies}

unresolved = [c for c in top_level if c['id'] not in replied_ids]

lines.append(f"Top-level comments: {len(top_level)}")
lines.append(f"Comments with replies: {len(top_level) - len(unresolved)}")
lines.append(f"UNRESOLVED (no reply): {len(unresolved)}")
lines.append("")

for c in unresolved:
    lines.append("=" * 70)
    lines.append(f"Comment ID: {c['id']}")
    lines.append(f"Author: {c['user']['login']}")
    lines.append(f"File: {c['path']}")
    lines.append(f"Line: {c.get('line') or c.get('original_line')}")
    lines.append(f"Created: {c['created_at']}")
    lines.append(f"Body:")
    lines.append(c['body'])
    lines.append("")

# Also fetch reviews
r3 = subprocess.run(
    ['gh', 'api', 'repos/daviburg/narrative-state-engine/pulls/435/reviews'],
    capture_output=True, text=True, encoding='utf-8',
    cwd=r'c:\Users\david\narrative-state-engine'
)
if r3.returncode == 0:
    reviews = json.loads(r3.stdout)
    lines.append("=" * 70)
    lines.append("")
    lines.append("Recent reviews (last 5):")
    for rev in reviews[-5:]:
        lines.append(f"  {rev['user']['login']} - {rev['state']} - {rev['submitted_at']}")

with open(outpath, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f"Output written to {outpath}")
