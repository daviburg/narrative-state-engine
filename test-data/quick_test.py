"""Quick test of B70 server response with v2 template."""
import json
import urllib.request
import time

system = "You are an entity extraction agent. Return JSON with key 'entities'."
user = 'Turn 201: The elder speaks. Return: {"entities": [{"existing_id": "char-elder", "confidence": 0.9}]}'
body = {
    "model": "qwen3-8b-int4-ov",
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    "max_tokens": 256,
    "temperature": 0.3,
}

print("Sending minimal test...", flush=True)
t0 = time.time()
req = urllib.request.Request(
    "http://192.168.10.169:8000/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=60)
data = resp.read().decode()
print(f"Status: {resp.status} ({time.time()-t0:.1f}s)", flush=True)
print(data[:500], flush=True)
