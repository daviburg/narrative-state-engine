#!/bin/bash
# Speed test for 30B MoE on GPU.1
cat > /tmp/test_req.json << 'ENDJSON'
{"model":"qwen3-30b-a3b-int4-ov","messages":[{"role":"user","content":"Count from 1 to 10"}],"max_tokens":50,"temperature":0}
ENDJSON

echo "=== Speed Test ==="
time curl -s http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d @/tmp/test_req.json | python3 -c "
import sys, json
r = json.loads(sys.stdin.read())
tokens = r['usage']['completion_tokens']
print(f'Completion tokens: {tokens}')
content = r['choices'][0]['message']['content']
print(f'Content: {content[:200]}')
"
echo "=== Thinking check ==="
grep -n 'enable_thinking\|thinking' /home/nse-agent/narrative-state-engine/server/ov_serve.py | head -10
