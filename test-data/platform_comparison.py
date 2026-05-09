"""Comprehensive v1 vs v5 template comparison on both B70 and RTX 4070.

Runs 8 turns × 3 runs × 2 templates on each platform.
Outputs structured results to test-data/platform-comparison.json.

Usage:
    python test-data/platform_comparison.py b70      # B70 only
    python test-data/platform_comparison.py rtx      # RTX 4070 only
    python test-data/platform_comparison.py both     # Both (sequential)
"""
import json, os, sys, time, statistics, copy
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from tools.catalog_merger import load_catalogs, format_known_entities_bounded, _estimate_tokens
from tools.discovery_baseline import load_turn, run_discovery, categorize_entities
from tools.llm_client import LLMClient

# --- Configuration ---
# Turn selection: mix of early-game, mid-game, late-game, entity-dense
TURNS = [201, 211, 221, 251, 300, 306, 312, 340]
RUNS_PER = 3

CATALOG_DIR = os.path.join(PROJECT_ROOT, "test-data", "catalogs")
TRANSCRIPT_DIR = os.path.join(PROJECT_ROOT, "test-data", "transcript")

# Template paths
V1_TEMPLATE = os.path.join(PROJECT_ROOT, "test-data", "entity-discovery-v1.md")
V5_TEMPLATE = os.path.join(PROJECT_ROOT, "templates", "extraction", "entity-discovery.md")

PLATFORMS = {
    "b70": {
        "config": os.path.join(PROJECT_ROOT, "test-data", "llm-b70-comparison.json"),
        "label": "B70 qwen3-8b",
        "temperature": 0.1,
        "output": os.path.join(PROJECT_ROOT, "test-data", "comparison-b70.json"),
    },
    "rtx": {
        "config": os.path.join(PROJECT_ROOT, "test-data", "llm-rtx4070.json"),
        "label": "RTX 4070 qwen3.5",
        "temperature": 0.05,
        "output": os.path.join(PROJECT_ROOT, "test-data", "comparison-rtx.json"),
    },
}


def count_formats(raw_text: str) -> tuple[int, int]:
    """Count compact vs full format entities in raw JSON text."""
    try:
        parsed = json.loads(raw_text)
        ents = parsed.get("entities", [])
        compact = sum(1 for e in ents if len(e) <= 3)
        full = sum(1 for e in ents if len(e) > 3)
        return compact, full
    except (json.JSONDecodeError, TypeError):
        return 0, 0


def urllib_extract_json(base_url, model, system_prompt, user_prompt, max_tokens, temperature, timeout_s=300):
    """Make a chat completion call using urllib (bypasses httpx entirely).
    
    Returns parsed JSON dict on success, raises on error.
    """
    import urllib.request
    import urllib.error

    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer not-needed",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    
    raw_text = data["choices"][0]["message"]["content"]
    finish_reason = data["choices"][0].get("finish_reason")
    
    if not raw_text:
        raise ValueError("Empty response from LLM")
    
    # Strip ```json fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    
    return json.loads(text), raw_text, finish_reason


def run_template_test(llm, template_text, template_name, turns_data, temperature, config, config_path=None):
    """Run all turns × runs for one template. Returns list of result dicts."""
    results = []
    discovery_max_tokens = config.get("discovery_max_tokens", config.get("max_tokens", 4096))
    
    # For B70/remote servers, use urllib to avoid httpx connection hangs
    use_urllib = config_path and "192.168" in config.get("base_url", "")

    for turn_num in TURNS:
        td = turns_data.get(turn_num)
        if td is None:
            continue

        for run in range(1, RUNS_PER + 1):
            label = f"  {template_name} turn={turn_num} run={run}/{RUNS_PER}"
            print(label, flush=True)

            if use_urllib:
                # Direct urllib path — no httpx, no OpenAI SDK
                from tools.discovery_baseline import format_discovery_prompt
                user_prompt = format_discovery_prompt(td["turn"], td["known_entities"])
                input_tokens = _estimate_tokens(template_text + user_prompt)
                
                print(f"  Calling LLM (max_tokens={discovery_max_tokens}, temp={temperature})...", flush=True)
                start = time.time()
                try:
                    parsed, raw_text, finish_reason = urllib_extract_json(
                        config["base_url"], config["model"],
                        template_text, user_prompt,
                        max_tokens=discovery_max_tokens,
                        temperature=temperature,
                        timeout_s=config.get("timeout_seconds", 300),
                    )
                    elapsed = time.time() - start
                    entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
                    output_tokens = _estimate_tokens(raw_text)
                    
                    result = {
                        "success": True,
                        "entities": entities,
                        "entity_count": len(entities),
                        "raw_text": raw_text,
                        "input_tokens_est": input_tokens,
                        "output_tokens_est": output_tokens,
                        "elapsed_s": round(elapsed, 1),
                        "truncated": finish_reason == "length",
                        "error": None,
                    }
                except Exception as e:
                    elapsed = time.time() - start
                    result = {
                        "success": False,
                        "entities": [],
                        "entity_count": 0,
                        "raw_text": "",
                        "input_tokens_est": input_tokens,
                        "output_tokens_est": 0,
                        "elapsed_s": round(elapsed, 1),
                        "truncated": False,
                        "error": f"{type(e).__name__}: {e}",
                    }
            else:
                result = run_discovery(
                    llm, td["turn"], td["known_entities"], template_text,
                    max_tokens=discovery_max_tokens,
                    temperature=temperature,
                )

            if result["success"]:
                cats = categorize_entities(result["entities"], td["turn"]["text"])
                result["active"] = len(cats["active"])
                result["passive"] = len(cats["passive"])
                result["spurious"] = len(cats["spurious"])
                result["spurious_names"] = [e.get("name", "?") for e in cats["spurious"]]

                compact, full = count_formats(result.get("raw_text", ""))
                result["compact_count"] = compact
                result["full_count"] = full

                status = f"[OK] {result['elapsed_s']}s | ents={result['entity_count']} " \
                         f"(active={result['active']}, passive={result['passive']}, " \
                         f"spur={result['spurious']}, compact={compact}) | ~{result['output_tokens_est']} tok"
                if result["spurious_names"]:
                    status += f"\n    Spurious: {', '.join(result['spurious_names'][:5])}"
            else:
                result["active"] = result["passive"] = result["spurious"] = 0
                result["compact_count"] = result["full_count"] = 0
                result["spurious_names"] = []
                status = f"[FAIL] {result['elapsed_s']}s: {result.get('error', '?')[:100]}"

            print(f"  {status}", flush=True)

            result["template"] = template_name
            result["turn"] = turn_num
            result["run"] = run
            result["temperature"] = temperature
            results.append(result)

    return results


def print_summary(results, platform_label):
    """Print comparison summary table."""
    print(f"\n{'='*80}")
    print(f"SUMMARY: {platform_label}")
    print(f"{'='*80}")

    templates = sorted(set(r["template"] for r in results))

    # Per-turn comparison
    print(f"\n{'Turn':>6} | ", end="")
    for t in templates:
        print(f"{'Tok':>6} {'Ent':>4} {'Spur':>4} {'Cmpct':>5} | ", end="")
    print(f"{'Δ Tokens':>10} {'Δ%':>6}")
    print("-" * 80)

    for turn_num in TURNS:
        print(f"{turn_num:>6} | ", end="")
        turn_data = {}
        for t in templates:
            runs = [r for r in results if r["turn"] == turn_num and r["template"] == t and r.get("success")]
            if runs:
                avg_tok = statistics.mean([r["output_tokens_est"] for r in runs])
                avg_ent = statistics.mean([r["entity_count"] for r in runs])
                avg_spur = statistics.mean([r["spurious"] for r in runs])
                avg_compact = statistics.mean([r["compact_count"] for r in runs])
                print(f"{avg_tok:>6.0f} {avg_ent:>4.1f} {avg_spur:>4.1f} {avg_compact:>5.1f} | ", end="")
                turn_data[t] = avg_tok
            else:
                print(f"{'FAIL':>6} {'--':>4} {'--':>4} {'--':>5} | ", end="")

        if len(turn_data) == 2 and all(t in turn_data for t in templates):
            delta = turn_data[templates[1]] - turn_data[templates[0]]
            pct = (delta / turn_data[templates[0]] * 100) if turn_data[templates[0]] else 0
            print(f"{delta:>+10.0f} {pct:>+5.0f}%")
        else:
            print()

    # Aggregate
    print("-" * 80)
    print(f"{'AVG':>6} | ", end="")
    for t in templates:
        succ = [r for r in results if r["template"] == t and r.get("success")]
        if succ:
            avg_tok = statistics.mean([r["output_tokens_est"] for r in succ])
            avg_ent = statistics.mean([r["entity_count"] for r in succ])
            avg_spur = statistics.mean([r["spurious"] for r in succ])
            avg_compact = statistics.mean([r["compact_count"] for r in succ])
            print(f"{avg_tok:>6.0f} {avg_ent:>4.1f} {avg_spur:>4.1f} {avg_compact:>5.1f} | ", end="")
        else:
            print(f"{'--':>6} {'--':>4} {'--':>4} {'--':>5} | ", end="")
    print()

    # Success/failure rate
    print(f"\nReliability:")
    for t in templates:
        total = len([r for r in results if r["template"] == t])
        succ = len([r for r in results if r["template"] == t and r.get("success")])
        fail = total - succ
        print(f"  {t}: {succ}/{total} success ({fail} failures)")

    # Compact format adoption
    print(f"\nCompact format adoption:")
    for t in templates:
        succ = [r for r in results if r["template"] == t and r.get("success")]
        if succ:
            compact_total = sum(r["compact_count"] for r in succ)
            full_total = sum(r["full_count"] for r in succ)
            total = compact_total + full_total
            if total > 0:
                print(f"  {t}: {compact_total}/{total} entities used compact format ({compact_total/total*100:.0f}%)")
            else:
                print(f"  {t}: no entities")


def main():
    platform_arg = sys.argv[1] if len(sys.argv) > 1 else "both"

    if platform_arg not in ("b70", "rtx", "both"):
        print(f"Usage: {sys.argv[0]} [b70|rtx|both]")
        sys.exit(1)

    platforms_to_run = ["b70", "rtx"] if platform_arg == "both" else [platform_arg]

    # Check v1 template exists
    if not os.path.exists(V1_TEMPLATE):
        print(f"V1 template not found at {V1_TEMPLATE}")
        print("Creating from git HEAD~1...")
        # We need v1 for comparison — save current production template as reference
        print("ERROR: v1 template must be saved manually. See README.")
        sys.exit(1)

    # Load templates
    with open(V1_TEMPLATE, "r", encoding="utf-8") as f:
        v1_text = f.read()
    with open(V5_TEMPLATE, "r", encoding="utf-8") as f:
        v5_text = f.read()

    print(f"V1 template: {_estimate_tokens(v1_text)} tokens est.")
    print(f"V5 template: {_estimate_tokens(v5_text)} tokens est.")

    # Load catalogs
    print(f"\nLoading catalogs from {CATALOG_DIR}...")
    catalogs = load_catalogs(CATALOG_DIR)
    total_entities = sum(len(v) for v in catalogs.values())
    print(f"  Loaded {total_entities} entities")

    for platform in platforms_to_run:
        pconf = PLATFORMS[platform]
        print(f"\n{'='*80}")
        print(f"PLATFORM: {pconf['label']}")
        print(f"{'='*80}")

        with open(pconf["config"], "r", encoding="utf-8") as f:
            config = json.load(f)

        llm = LLMClient(pconf["config"])
        temp = pconf["temperature"]
        print(f"  LLM: {config.get('base_url')} / {config.get('model')}")
        print(f"  Temperature: {temp}")

        # Precompute turn data
        context_length = config.get("context_length", 32768)
        turns_data = {}
        for turn_num in TURNS:
            turn = load_turn(TRANSCRIPT_DIR, turn_num)
            if turn is None:
                continue
            known = format_known_entities_bounded(
                catalogs, current_turn=turn_num,
                context_length=context_length,
                turn_text=turn["text"],
            )
            turns_data[turn_num] = {"turn": turn, "known_entities": known}
            print(f"  Turn {turn_num}: {_estimate_tokens(turn['text'])} turn tok, "
                  f"{_estimate_tokens(known)} known-entity tok")

        # Run v1
        print(f"\n--- V1 (original template) ---")
        v1_results = run_template_test(llm, v1_text, "v1", turns_data, temp, config, config_path=pconf["config"])

        # Run v5
        print(f"\n--- V5 (optimized template) ---")
        v5_results = run_template_test(llm, v5_text, "v5", turns_data, temp, config, config_path=pconf["config"])

        all_results = v1_results + v5_results

        # Save results
        with open(pconf["output"], "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to {pconf['output']}")

        # Print summary
        print_summary(all_results, pconf["label"])

    print(f"\n{'='*80}")
    print("DONE")


if __name__ == "__main__":
    main()
