"""Quick script to check the run.log and extraction-log.jsonl status."""
import json, os

out = []

# Check extraction log
logf = 'framework-local/ab-test/ctx-baseline/extraction-log.jsonl'
if os.path.exists(logf):
    lines = open(logf).readlines()
    out.append(f"Extraction log: {len(lines)} turns")
    for l in lines:
        j = json.loads(l)
        out.append(f"  {j['turn_id']}: {j['elapsed_ms']}ms, new_ent={j['new_entities']}, new_evt={j['new_events']}")
else:
    out.append("No extraction log")

# Check run log
runf = 'framework-local/ab-test/ctx-baseline/run.log'
if os.path.exists(runf):
    data = open(runf, 'rb').read()
    if data[:2] == b'\xff\xfe':
        text = data[2:].decode('utf-16-le', errors='replace')
    else:
        text = data.decode('utf-8', errors='replace')
    lines = text.splitlines()
    out.append(f"\nRun log: {len(lines)} lines")
    out.append("--- Last 40 lines ---")
    for l in lines[-40:]:
        out.append(l)
else:
    out.append("No run log")

# Write output to file
with open('_check_log_output.txt', 'w') as f:
    f.write('\n'.join(out))
