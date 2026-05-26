import subprocess, os

repo = r"C:\Users\david\narrative-state-engine"
os.chdir(repo)
out_file = os.path.join(repo, "_status_output.txt")

lines = []
lines.append("=== GIT STATUS (--porcelain -u) ===")
result = subprocess.run(["git", "status", "--porcelain", "-u"], capture_output=True, text=True)
if result.stdout.strip():
    lines.append(result.stdout.rstrip())
else:
    lines.append("(clean - no output)")
if result.stderr.strip():
    lines.append("STDERR: " + result.stderr.rstrip())

lines.append("")
lines.append("=== FILE EXISTENCE CHECKS ===")
paths = [
    "config/llm-4070.json",
    "config/llm.json.bak",
    "docs/ab-test-checklist.md",
    "docs/ab-test-standard.md",
    "ab-a-run1.log",
    "framework-ab-a-run1",
    "memories/session",
]
for p in paths:
    full = os.path.join(repo, p)
    exists = os.path.exists(full)
    kind = ""
    if exists:
        kind = " (dir)" if os.path.isdir(full) else " (file)"
    lines.append(f"  {p}: {exists}{kind}")

lines.append("")
lines.append("=== GIT BRANCH ===")
result2 = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
lines.append(f"  {result2.stdout.strip()}")

with open(out_file, "w") as f:
    f.write("\n".join(lines))
print("Written to", out_file)
