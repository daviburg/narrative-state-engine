import json, sys
data = json.load(sys.stdin)
for c in data:
    body_first = c["body"].split("\n")[0][:100]
    print(f'{c["id"]} {c["path"]}:{c.get("line","?")} {body_first}')
