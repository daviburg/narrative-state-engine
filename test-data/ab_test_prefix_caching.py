"""A/B test for issue #318: prefix caching stall after identical prompts.

Tests the exact reproduction pattern from the issue:
  1. Send 3 identical requests (same prompt × 3)
  2. Send 1 different request

With prefix caching enabled (bug), step 2 hangs indefinitely.
With prefix caching disabled (fix), all 4 requests complete.

Usage:
  python test-data/ab_test_prefix_caching.py --server http://192.168.10.169:8000

The script uses a short timeout (60s) per request so it doesn't hang forever
if the bug is present.  A passing run completes all 4 requests within that
time; a failing run reports a timeout on the 4th request.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def chat_request(server: str, content: str, timeout: int = 60) -> dict:
    """Send a chat completion request and return (response_dict, elapsed_s)."""
    url = f"{server}/v1/chat/completions"
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": "You are a JSON extraction assistant."},
            {"role": "user", "content": content},
        ],
        "max_tokens": 512,
        "temperature": 0.05,
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    resp = urllib.request.urlopen(req, timeout=timeout)
    elapsed = time.perf_counter() - start
    data = json.loads(resp.read())
    return data, elapsed


def health_check(server: str) -> dict:
    """Check server health and return the response."""
    url = f"{server}/health"
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="A/B test for #318 prefix caching stall")
    parser.add_argument("--server", default="http://192.168.10.169:8000",
                        help="Server URL (default: arclight B70)")
    parser.add_argument("--timeout", type=int, default=60,
                        help="Per-request timeout in seconds (default: 60)")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    timeout = args.timeout

    # Step 0: Health check
    print(f"=== A/B Test: Prefix Caching Stall (#318) ===")
    print(f"Server: {server}")
    try:
        health = health_check(server)
        print(f"Health: {json.dumps(health)}")
        prefix_caching = health.get("prefix_caching", "unknown")
        print(f"Prefix caching: {prefix_caching}")
    except Exception as e:
        print(f"FAIL: Server unreachable: {e}")
        sys.exit(1)

    print()

    # The identical prompt (simulates repeated extraction of the same turn)
    identical_prompt = (
        "Extract entities from this turn:\n\n"
        "DM: The forest path narrows as you approach the ancient ruins. "
        "Moss-covered stones line the trail, and you can hear the distant "
        "sound of running water. Lyrawyn notices scratch marks on the trees."
    )

    # The different prompt (simulates switching to a new turn)
    different_prompt = (
        "Extract entities from this turn:\n\n"
        "DM: The merchant's wagon creaks to a halt at the crossroads. "
        "Borin adjusts his pack and squints at the faded signpost. "
        "'Three days to Thornwall,' he mutters."
    )

    # Step 1: Send 3 identical requests
    print("--- Phase 1: 3× identical prompt ---")
    for i in range(3):
        try:
            data, elapsed = chat_request(server, identical_prompt, timeout)
            tokens = data["usage"]["completion_tokens"]
            print(f"  Request {i+1}/3: OK  ({tokens} tokens, {elapsed:.1f}s)")
        except urllib.error.URLError as e:
            print(f"  Request {i+1}/3: FAIL — {e}")
            sys.exit(1)
        except TimeoutError:
            print(f"  Request {i+1}/3: TIMEOUT after {timeout}s")
            sys.exit(1)

    print()

    # Step 2: Send 1 different request (this is the one that hangs with bug)
    print("--- Phase 2: 1× different prompt (stall trigger) ---")
    try:
        data, elapsed = chat_request(server, different_prompt, timeout)
        tokens = data["usage"]["completion_tokens"]
        print(f"  Request 4/4: OK  ({tokens} tokens, {elapsed:.1f}s)")
    except urllib.error.URLError as e:
        print(f"  Request 4/4: FAIL — {e}")
        print(f"\n*** BUG CONFIRMED: Pipeline stalled after switching prompts ***")
        sys.exit(1)
    except TimeoutError:
        print(f"  Request 4/4: TIMEOUT after {timeout}s")
        print(f"\n*** BUG CONFIRMED: Pipeline stalled after switching prompts ***")
        sys.exit(1)

    print()
    print("=== ALL REQUESTS COMPLETED — no stall detected ===")

    # Step 3: Bonus — send 2 more alternating requests to confirm stability
    print()
    print("--- Phase 3: 2× alternating prompts (stability check) ---")
    for i, prompt in enumerate([identical_prompt, different_prompt], start=5):
        try:
            data, elapsed = chat_request(server, prompt, timeout)
            tokens = data["usage"]["completion_tokens"]
            print(f"  Request {i}/6: OK  ({tokens} tokens, {elapsed:.1f}s)")
        except Exception as e:
            print(f"  Request {i}/6: FAIL — {e}")
            sys.exit(1)

    print()
    print("=== FULL PASS — 6/6 requests completed successfully ===")


if __name__ == "__main__":
    main()
