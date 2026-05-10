"""Tools for running inference benchmarks."""

import subprocess
import time
from pathlib import Path

from crewai.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[2]


@tool("Benchmark llama-server throughput")
def benchmark_llama_server(
    prompt: str = "Tell me about the history of dragons in fantasy literature.",
    n_predict: int = 256,
    iterations: int = 3,
) -> str:
    """Benchmark llama-server by sending completion requests and measuring tok/s.

    Args:
        prompt: Test prompt to send
        n_predict: Number of tokens to generate
        iterations: Number of benchmark iterations
    """
    import json
    import urllib.request

    results = []
    for i in range(iterations):
        payload = json.dumps({
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": 0.0,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:8080/completion",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        elapsed = time.time() - start

        tokens = data.get("tokens_predicted", n_predict)
        tok_s = tokens / elapsed if elapsed > 0 else 0
        results.append({"run": i + 1, "tokens": tokens, "elapsed_s": round(elapsed, 2), "tok_s": round(tok_s, 1)})

    avg_tok_s = sum(r["tok_s"] for r in results) / len(results)
    lines = ["| Run | Tokens | Elapsed (s) | tok/s |", "|-----|--------|-------------|-------|"]
    for r in results:
        lines.append(f"| {r['run']} | {r['tokens']} | {r['elapsed_s']} | {r['tok_s']} |")
    lines.append(f"\n**Average: {round(avg_tok_s, 1)} tok/s**")
    return "\n".join(lines)


@tool("Benchmark Ollama throughput")
def benchmark_ollama(
    model: str = "qwen2.5:14b-8k",
    prompt: str = "Tell me about the history of dragons in fantasy literature.",
    iterations: int = 3,
) -> str:
    """Benchmark Ollama model inference speed.

    Args:
        model: Ollama model name
        prompt: Test prompt to send
        iterations: Number of benchmark iterations
    """
    import json
    import urllib.request

    results = []
    for i in range(iterations):
        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        elapsed = time.time() - start

        eval_count = data.get("eval_count", 0)
        eval_duration_ns = data.get("eval_duration", elapsed * 1e9)
        tok_s = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0
        results.append({"run": i + 1, "tokens": eval_count, "elapsed_s": round(elapsed, 2), "tok_s": round(tok_s, 1)})

    avg_tok_s = sum(r["tok_s"] for r in results) / len(results)
    lines = [f"**Model: {model}**\n", "| Run | Tokens | Elapsed (s) | tok/s |", "|-----|--------|-------------|-------|"]
    for r in results:
        lines.append(f"| {r['run']} | {r['tokens']} | {r['elapsed_s']} | {r['tok_s']} |")
    lines.append(f"\n**Average: {round(avg_tok_s, 1)} tok/s**")
    return "\n".join(lines)
