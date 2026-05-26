import subprocess, json, sys, os

os.chdir(r"c:\Users\david\narrative-state-engine")

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return r.stdout, r.stderr

print("=" * 60)
print("PR #435 STATUS")
print("=" * 60)
out, err = run("gh pr view 435 --json number,title,state,headRefName,updatedAt,reviewDecision,mergedAt")
print(out or err)

print("\n" + "=" * 60)
print("PR #435 REVIEWS")
print("=" * 60)
out, err = run('gh api repos/daviburg/narrative-state-engine/pulls/435/reviews')
reviews = json.loads(out) if out.strip() else []
for r in reviews:
    print(json.dumps({"id": r["id"], "user": r["user"]["login"], "state": r["state"], "submitted_at": r["submitted_at"]}, indent=2))

print("\n" + "=" * 60)
print("PR #435 COMMENTS (top-level only)")
print("=" * 60)
out, err = run('gh api repos/daviburg/narrative-state-engine/pulls/435/comments')
comments = json.loads(out) if out.strip() else []
for c in comments:
    if c.get("in_reply_to_id") is None:
        print(json.dumps({"id": c["id"], "path": c["path"], "body": c["body"], "created_at": c["created_at"], "user": c["user"]["login"]}, indent=2))
        print("---")

print("\n" + "=" * 60)
print("PR #436 STATUS")
print("=" * 60)
out, err = run("gh pr view 436 --json number,title,state,headRefName,updatedAt,reviewDecision,mergedAt")
print(out or err)

print("\n" + "=" * 60)
print("PR #436 REVIEWS")
print("=" * 60)
out, err = run('gh api repos/daviburg/narrative-state-engine/pulls/436/reviews')
reviews = json.loads(out) if out.strip() else []
for r in reviews:
    print(json.dumps({"id": r["id"], "user": r["user"]["login"], "state": r["state"], "submitted_at": r["submitted_at"]}, indent=2))

print("\n" + "=" * 60)
print("PR #436 COMMENTS (top-level only)")
print("=" * 60)
out, err = run('gh api repos/daviburg/narrative-state-engine/pulls/436/comments')
comments = json.loads(out) if out.strip() else []
for c in comments:
    if c.get("in_reply_to_id") is None:
        print(json.dumps({"id": c["id"], "path": c["path"], "body": c["body"], "created_at": c["created_at"], "user": c["user"]["login"]}, indent=2))
        print("---")
