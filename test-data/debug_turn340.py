"""Debug turn 340: capture raw LLM response for v1 and v2 templates."""
import os, sys, json, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from llm_client import LLMClient
from catalog_merger import load_catalogs, format_known_entities_bounded
from semantic_extraction import load_template, format_discovery_prompt

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
CATALOG_DIR = os.path.join(PROJECT_ROOT, "test-data", "catalogs")
TRANSCRIPT_DIR = os.path.join(PROJECT_ROOT, "test-data", "transcript")
CONFIG = os.path.join(PROJECT_ROOT, "test-data", "llm-rtx4070.json")

def load_turn(turn_num):
    path = os.path.join(TRANSCRIPT_DIR, f"turn-{turn_num}-dm.md")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return {"turn_id": f"turn-{turn_num}", "text": text, "speaker": "DM"}

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--turn", type=int, default=340)
    args = parser.parse_args()

    catalogs = load_catalogs(CATALOG_DIR)
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    with open(args.template, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    turn = load_turn(args.turn)
    known = format_known_entities_bounded(
        catalogs, current_turn=args.turn,
        context_length=config.get("context_length", 32768),
        turn_text=turn["text"],
    )

    user_prompt = format_discovery_prompt(turn, known)
    llm = LLMClient(args.config)

    print(f"Template: {os.path.basename(args.template)}")
    print(f"Turn: {args.turn}")
    print(f"System prompt tokens est: {len(system_prompt) // 4}")
    print(f"User prompt tokens est: {len(user_prompt) // 4}")
    print(f"Calling LLM...")
    sys.stdout.flush()

    start = time.time()
    try:
        # Use raw chat completion to get unprocessed response
        response = llm.client.chat.completions.create(
            model=config.get("model", "qwen3.5"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=config.get("discovery_max_tokens", 8192),
            temperature=config.get("discovery_temperature", 0.3),
        )
        elapsed = time.time() - start
        raw = response.choices[0].message.content
        finish = response.choices[0].finish_reason
        print(f"Elapsed: {elapsed:.1f}s")
        print(f"Finish reason: {finish}")
        print(f"Raw length: {len(raw)} chars, ~{len(raw)//4} tokens")
        print(f"\n{'='*80}")
        print(f"RAW RESPONSE:")
        print(f"{'='*80}")
        print(raw)
    except Exception as e:
        elapsed = time.time() - start
        print(f"ERROR after {elapsed:.1f}s: {type(e).__name__}: {e}")
        partial = getattr(e, "partial_text", None) or getattr(e, "response", None)
        if partial:
            print(f"Partial ({len(str(partial))} chars):")
            print(str(partial)[:5000])

if __name__ == "__main__":
    main()
