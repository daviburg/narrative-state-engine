# Usage Guide

## Setup

### Prerequisites

- Python 3.10+
- No external Python packages required for core tools
- **Optional** — for LLM-based semantic extraction:
  - `pip install -r requirements-llm.txt`
  - An OpenAI-compatible LLM endpoint (llama-server, OpenAI API, Ollama, etc.)
  - Configure `config/llm.json` with provider, model, and endpoint

### Creating a New Session

Create a session directory and metadata file:

```bash
mkdir -p sessions/session-001/{transcript,raw,derived}

cat > sessions/session-001/metadata.json << 'EOF'
{
  "session_id": "session-001",
  "title": "Session 1",
  "start_date": "2026-01-01",
  "description": "Opening session",
  "turn_count": 0
}
EOF
```

---

## LLM Model Requirements

Semantic extraction uses an LLM to identify entities, relationships, and events from transcript text. The quality of extraction depends significantly on model size.

### Minimum Requirements

- **Minimum**: 7B parameters — basic entity extraction works but expect some ID format errors and weak coreference resolution
- **Recommended**: 14B+ parameters — reliable JSON output, good coreference, accurate entity classification
- **Best**: GPT-4o, Gemini Flash, or equivalent cloud model — highest accuracy but requires API key and costs per token

### Tested Models

| Model | Parameters | Quantization | VRAM | Extraction Quality |
|---|---|---|---|---|
| `qwen2.5:3b` | 3B | Q4_K_M | ~2.5 GB | Poor — 0% item recall, frequent hallucinations, ID format violations |
| `qwen2.5:14b` | 14B | Q4_K_M | ~9 GB | Good — reliable entity classification, items extracted, acceptable coreference |
| `Qwen3.5-9B-Q4_K_M` (llama-server) | 9B | Q4_K_M | ~5.3 GB | Very good — significant quality jump over qwen2.5:14b despite fewer parameters. Reliable JSON, accurate entity typing, strong coreference. Thinking mode disabled via `--reasoning-format none`. See [quality notes](#qwen35-9b-quality-observations) below. |
| `qwen3-8b-int4-ov` (OpenVINO) | 8B | INT4 sym | ~4.5 GB | Good — reliable JSON, needs `skip_response_format`, thinking suppressed via `enable_thinking=False` in chat template |
| `gemini-2.5-flash` (Google) | Unknown | N/A | Cloud | Very good — fast, low cost (~$0.30/run), 1M token context, strong JSON mode |
| `gpt-4o` (OpenAI) | Unknown | N/A | Cloud | Best — accurate structured JSON, strong coreference, minimal hallucination |

### VRAM Quick Reference

| GPU VRAM | Maximum Model Size (Q4 quantization) |
|---|---|
| 8 GB | Up to 7B (up to 9B with reduced context) |
| 12 GB | Up to 14B |
| 16 GB | Up to 22B |
| 24 GB | Up to 32B |
| 31 GB (Arc Pro B70) | Up to 70B (INT4 sym, OpenVINO) |

### Qwen3.5-9B Quality Observations

Qwen3.5-9B (Q4_K_M quantization, served via llama-server) delivers a measurable quality improvement over the previous best local model (qwen2.5:14b) despite having fewer parameters:

- **Entity classification accuracy** — fewer type-prefix errors (e.g., items no longer misclassified as locations)
- **Coreference resolution** — significantly better at recognizing when different descriptions refer to the same entity, reducing duplicate catalog entries
- **Relationship detection** — captures more nuanced inter-entity relationships with appropriate confidence scores
- **JSON compliance** — reliable structured output without needing `response_format` enforcement. On llama-server, use `--reasoning-format none` to fully disable thinking blocks (unlike qwen3-8b where `<think>` blocks cannot be disabled at the server level). This is a Qwen3.5-specific improvement.
- **Context efficiency** — the 9B dense architecture uses context more effectively than the 14B qwen2.5 at equivalent quantization

This model is the recommended choice for local extraction when a GPU with 12+ GB VRAM is available. On an 8 GB GPU it fits with reduced context (~8K). On the RTX 4070 (12 GB), it runs at ~61 tok/s with room for a 32K context window. On the Intel Arc Pro B70 (31 GB), it achieves ~56 tok/s via SYCL.

### Known Limitations of Small Models (<7B)

- Entity IDs generated with wrong type prefixes (e.g., `loc-` for an item)
- Poor coreference resolution — same entity gets multiple catalog entries
- Items rarely or never extracted
- Location/character/faction type confusion
- Hallucinated entities not present in the source text

### Using Google Gemini

Google Gemini models are accessible via an OpenAI-compatible endpoint. Set up
an API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey),
then configure `config/llm.json`:

```json
{
  "provider": "openai",
  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
  "model": "gemini-2.5-flash",
  "api_key_env": "GEMINI_API_KEY",
  "temperature": 0.0,
  "max_tokens": 4096,
  "pc_max_tokens": 8192,
  "timeout_seconds": 60,
  "retry_attempts": 3,
  "batch_delay_ms": 100
}
```

Set the environment variable before running extraction:

```powershell
# PowerShell — persist across sessions
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-key-here", "User")
```

```bash
# Bash / sh
export GEMINI_API_KEY="your-key-here"
```

> **Note:** Gemini uses the same OpenAI-compatible client path as GPT-4o.
> No Ollama-specific options are needed. The `context_length` field is
> ignored for cloud providers (Gemini manages context internally with up
> to 1M tokens). The pipeline auto-detects cloud providers and enforces
> a minimum 2000ms inter-call delay to avoid per-minute rate limits.
> If consecutive 429 errors hit the `consecutive_rate_limit_threshold`
> (default 10), the pipeline stops and saves progress rather than burning
> through daily quota on retries.

### Using a Local Model

The recommended local backend is **llama-server** (from [llama.cpp](https://github.com/ggml-org/llama.cpp)), which exposes an OpenAI-compatible `/v1` endpoint with true parallel slot processing. Download a pre-built release from the [llama.cpp releases page](https://github.com/ggml-org/llama.cpp/releases) — CUDA, Vulkan, and SYCL builds are available.

```bash
# Single-slot baseline (simplest setup)
llama-server -m Qwen3.5-9B-Q4_K_M.gguf \
    -ngl 999 -c 32768 --flash-attn on -t 1 -np 1 --port 8080 \
    --reasoning-format none

# Parallel setup (4 concurrent extraction phases)
llama-server -m Qwen3.5-9B-Q4_K_M.gguf \
    -ngl 999 -c 32768 --flash-attn on -t 1 -np 4 --port 8080 \
    --reasoning-format none
```

Configure `config/llm.json` (parallel example):

```json
{
  "provider": "openai",
  "base_url": "http://localhost:8080/v1",
  "model": "Qwen3.5-9B-Q4_K_M",
  "api_key_env": "",
  "temperature": 0.0,
  "top_k": 1,
  "top_p": 1.0,
  "min_p": 0.0,
  "seed": 42,
  "max_tokens": 4096,
  "pc_max_tokens": 8192,
  "context_length": 32768,
  "timeout_seconds": 120,
  "retry_attempts": 3,
  "batch_delay_ms": 0,
  "parallel_workers": 4
}
```

#### Deterministic Sampling (#471)

For reproducible extraction, pin **all** sampler parameters, not just
temperature. The llama-server baked default is `temperature 1.0` with
`top_k 20 / top_p 0.95 / min_p 0.05` and a random seed, so a config that
omits these inherits stochastic sampling even when it *looks* greedy.

| Key | Greedy value | Effect |
|---|---|---|
| `temperature` | `0` | disables temperature scaling (argmax) |
| `top_k` | `1` | keep only the single most-likely token |
| `top_p` | `1.0` | no nucleus truncation (redundant with `top_k: 1` but explicit) |
| `min_p` | `0.0` | no minimum-probability floor |
| `seed` | `42` | pins RNG (irrelevant under pure greedy, but logged for provenance) |

`top_p` and `seed` are native OpenAI parameters; `top_k` and `min_p` are sent
via `extra_body` (llama-server reads them). All five keys are optional — when
omitted the client sends nothing for them and the backend default applies.

With this config on a **single pinned GPU endpoint**, single-GPU extraction is
byte-deterministic (empirically 8/8 byte-identical at 512 tokens). The
remaining non-determinism source is **cross-GPU round-robin** (`base_urls`
across non-bit-identical GPUs) — pin one endpoint when byte reproducibility
matters.

On startup the client logs a `[sampler]` line to stderr showing the effective
client-sent sampling (model, temperature, top_k, top_p, min_p, seed, max_tokens,
endpoint) and, for local backends, probes the server's `/props` endpoint and
logs the server-side `default_generation_settings`. Capture this log in A/B run
provenance to verify the run actually used the intended sampler config.


Key flags:

- **`-ngl 999`** — offload all layers to GPU
- **`-c 32768`** — context window size (adjust to fit VRAM)
- **`--reasoning-format none`** — disable thinking mode for Qwen3.5 (avoids `<think>` blocks consuming output tokens)
- **`-np N`** — parallel slots for concurrent requests (use with `parallel_workers` in `config/llm.json`)
- **`--flash-attn on`** — enable flash attention (slight memory savings)

> **Why llama-server over Ollama?** llama-server gives direct control over
> context size, parallel slots, and reasoning mode via CLI flags. Ollama
> wraps llama.cpp but requires Modelfile variants for context size changes,
> serializes concurrent requests on consumer GPUs, and its `/v1` endpoint
> ignores runtime `num_ctx` overrides. For extraction workloads, llama-server
> delivers measurably higher throughput.

### Fallback Provider

When the primary LLM exhausts all `retry_attempts`, the client can
automatically route the request to a fallback provider. Configure
a `"fallback"` block inside `config/llm.json`:

```json
{
  "provider": "openai",
  "base_url": "http://localhost:8000/v1",
  "model": "qwen3-8b-int4-ov",
  "timeout_seconds": 600,
  "retry_attempts": 3,
  "skip_response_format": true,
  "fallback": {
    "base_url": "http://localhost:8081/v1",
    "model": "qwen3.5-9b-q4_k_m",
    "timeout_seconds": 300,
    "skip_response_format": true
  }
}
```

The `fallback` block accepts the same keys as the top-level config.
Any key in the fallback overrides the primary value; unspecified keys
inherit from the primary config. `LLMTruncationError` and
`QuotaExhaustedError` are never retried through the fallback — they
propagate immediately.

### Context Optimizations

As entity catalogs grow, extraction prompts can exceed the model's context
window. The pipeline applies three budget-control optimizations automatically
to trim prompt content. These are always active and require no configuration.

| Optimization | Effect |
|---|---|
| **Relationship relevance scoring** | Applies a 3-tier priority system to relationship context in the relationship-mapper prompt. Tier 1 (full history) for both endpoints mentioned in the current turn; tier 2 (current + last update) for one endpoint mentioned + recently updated; tier 3 (summary only) for one endpoint mentioned + active. Dormant/resolved relationships are omitted unless both endpoints are mentioned. Token budget: 20% of `context_length`. |
| **Arc-aware compression** | Extends the PC-only volatile-state digest and relationship-history trimming to all entities. History arrays are capped to 3 entries per key; entries older than 50 turns are digested to a summary line; relationship histories are trimmed to last 3 entries. |
| **Scene-scoped detail** | Trims non-PC catalog entries in the entity-detail prompt: volatile state is digested and capped, relationships are filtered to mentioned + recent (20 turns) and capped at 15, stable attributes are preserved in full. |

Monitor the `prompt_metrics` field in `extraction-log.jsonl` to verify
budget compliance.  The `turn_compression` field records per-turn raw and
compressed token totals and lists which phases were active.

To aggregate across a session and bucket by turn-index band (1-20, 21-50,
51-100, 101+), use `tools/agg_compression.py`:

```bash
python tools/agg_compression.py sessions/<name>/framework/extraction-log.jsonl

# Compare two runs side-by-side:
python tools/agg_compression.py --label A run_a/extraction-log.jsonl \
                                 --label B run_b/extraction-log.jsonl
```

The `--label` flag applies to the immediately following path; paths without a
preceding `--label` are displayed using their file path as the title.

Turn-band bucketing reveals late-session prompt growth that session-total
averages mask.  A healthy session shows a flat or slowly rising ratio across
all bands.  A ratio approaching 1.0 in the 51-100 and 101+ bands with
`activated_phases` empty indicates the compression surfaces were never
triggered; a ratio well below 1.0 in those bands confirms active compression.

### Timeout Watchdog

The LLM client includes a wall-clock watchdog that prevents indefinite hangs
when the LLM server stalls (e.g., GPU lockup, network issue mid-stream). The
watchdog fires after `timeout_seconds × 3` seconds of total elapsed time and
force-closes the connection, converting the hang into a retriable error.

- **Ollama streaming path**: A `threading.Timer` force-closes the HTTP
  connection if no data arrives within the deadline.
- **OpenAI-compat path**: The SDK call is wrapped in a thread with a hard
  wall-clock deadline via a daemon thread.

When the watchdog fires, you'll see a log line:

```
  WATCHDOG: aborting stalled Ollama stream after 180s
  WATCHDOG: LLM call exceeded 180s wall-clock deadline
```

The error is caught by the normal retry loop, so stalled connections are
automatically retried up to `retry_attempts` times. No configuration is
needed — the watchdog is always active and transparent to callers.

### Using Ollama

Ollama is an alternative local backend. Configure `config/llm.json`:

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434/v1",
  "model": "qwen2.5:14b-8k",
  "api_key_env": "",
  "temperature": 0.0,
  "max_tokens": 4096,
  "pc_max_tokens": 8192,
  "context_length": 8192,
  "timeout_seconds": 120,
  "retry_attempts": 3,
  "batch_delay_ms": 200,
  "parallel_workers": 4
}
```

> **Note:** Ollama exposes an OpenAI-compatible `/v1` endpoint, so the tooling connects to it through the OpenAI-compatible client path. Set `"provider": "ollama"` when targeting Ollama to enable Ollama-specific request options (`extra_body.options`). The default Ollama port (`:11434`) is also auto-detected regardless of the `provider` value.

> **Self-hosted backends behind a public address:** non-standard samplers (`top_k`, `min_p`) ride in `extra_body`, which only self-hosted OpenAI-compatible backends (llama-server, vLLM) accept — cloud APIs reject them, so they are dropped there. The provider is classified from the `base_url` (local/loopback/RFC1918 ⇒ self-hosted). If your self-hosted server is reachable by a public DNS name or public IP, set `"self_hosted": true` so `top_k`/`min_p` are still forwarded; set `"self_hosted": false` to force cloud handling. Known self-hosted `provider` names (`llama-server`, `vllm`, `tgi`, `local`, …) are also treated as self-hosted regardless of URL.

### Setting the Context Size (Ollama)

Ollama's OpenAI-compatible `/v1` endpoint **ignores** runtime `num_ctx`
overrides. To set a custom context length you must create a model variant via
a Modelfile. Pre-tuned Modelfiles are provided in `config/ollama/`:

```bash
# Pull the base model
ollama pull qwen2.5:14b

# Create a variant (pick one that fits your GPU)
ollama create qwen2.5:14b-8k -f config/ollama/qwen2.5-14b-8k.Modelfile
```

| Variant | Context | VRAM (approx) | GPU |
|---------|---------|---------------|-----|
| `qwen2.5:14b-4k` | 4 096 | ~9.1 GB | 8 GB (tight) |
| **`qwen2.5:14b-8k`** | **8 192** | **~9.8 GB** | **12 GB (recommended)** |
| `qwen2.5:14b-16k` | 16 384 | ~11.2 GB | 16 GB+ |

After creating the variant, update `model` in `config/llm.json` to match.
The Modelfile sets the model's effective default context size permanently.
The `context_length` field in `config/llm.json` is also sent to Ollama via
`extra_body.options.num_ctx` as a runtime override (#175), but Ollama's
OpenAI-compatible `/v1` endpoint may ignore that override. Use a Modelfile
variant when you need context-size changes to take effect reliably across
restarts.

```json
{
  "model": "qwen2.5:14b-8k"
}
```

> **Why does this matter?** With the default 4K context, longer DM turns and
> PC entity prompts are silently truncated, degrading extraction quality.
> Using 8K context on an RTX 4070 (12 GB) yields a ~4–5× throughput improvement
> over the broken-default scenario and eliminates most timeout failures.

See `config/ollama/README.md` for details on adding variants for other base
models.

### Using llama-server on Intel Arc (SYCL)

llama.cpp supports Intel Arc GPUs via the SYCL backend. Pre-built binaries
are available from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
(look for `ubuntu-sycl-fp16-x64`). A Vulkan build is also available as a
fallback but runs ~34% slower.

#### Launch (Intel Arc Pro B70)

```bash
export LD_LIBRARY_PATH="$HOME/llama-b9010-sycl/llama-b9010:/opt/intel/oneapi/redist/lib:/opt/intel/oneapi/umf/1.0/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export ONEAPI_DEVICE_SELECTOR=level_zero:0
export ZES_ENABLE_SYSMAN=1
export UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1

~/llama-b9010-sycl/llama-b9010/llama-server \
    -m /path/to/Qwen3.5-9B-Q4_K_M.gguf \
    --host 127.0.0.1 --port 8080 -ngl 999 -c 32768 \
    --flash-attn on -t 1 -np 1 \
    --reasoning-format none
```

Environment variables provide ~13% speedup over defaults:

- **`UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1`** — allows larger GPU allocations
- **`ONEAPI_DEVICE_SELECTOR=level_zero:0`** — pin to the discrete GPU
- **`ZES_ENABLE_SYSMAN=1`** — enable system management interface

> **Remote access:** To serve requests from other machines, change `--host 127.0.0.1`
> to `--host 0.0.0.0`. llama-server has no authentication — only bind to all
> interfaces on trusted networks or behind a firewall.

#### B70 Performance (Qwen3.5-9B Q4_K_M, 5.3 GB)

| Backend | Raw Decode (tok/s) | Server (tok/s) | PP512 (tok/s) |
|---|---|---|---|
| SYCL FP16 | 60 | 56 | 2267 |
| Vulkan (Mesa ANV) | 40.5 | 37 | 1597 |

The B70 is memory-bandwidth limited at ~60 tok/s for this model.
No runtime parameter changes (KV quant, batch size, thread count, flash
attention) break this ceiling. Lighter quantizations (IQ4_XS, Q3_K_M)
are the only lever for faster decode — see [issue #284](https://github.com/daviburg/narrative-state-engine/issues/284).

#### Vulkan Fallback

A Vulkan build (`ubuntu-vulkan-x64` release asset) works on the B70 via
the Mesa ANV driver. It starts faster (no SYCL JIT warmup) but is 34%
slower. Keep it deployed as a fallback for when SYCL support breaks or a
new model architecture isn't supported in the SYCL backend.

### Using OpenVINO (Intel Arc / Xeon GPUs)

OpenVINO's `ContinuousBatchingPipeline` provides high-throughput inference on
Intel Arc discrete GPUs and Xeon CPUs. This section documents tested hardware
configurations and the concurrency constraints that matter for extraction.

#### Tested Hardware

| GPU | VRAM | Max Model | Batch Perf (INT4) | Notes |
|---|---|---|---|---|
| Intel Arc Pro B70 | 31 GB | 70B INT4 | ~61 tok/s (batch=1), ~204 agg (batch=4) | Server-class; 8 GB KV cache fits large batches |
| NVIDIA RTX 4070 | 12 GB | 14B Q4 | ~40-50 tok/s (llama-server, batch=1) | Consumer-class; use llama-server (`-np 4`) or Ollama |

#### Server Setup (Intel Arc + OpenVINO)

The extraction pipeline connects to any OpenAI-compatible endpoint. For Intel
Arc GPUs, serve the model with OpenVINO's `ContinuousBatchingPipeline` behind
a FastAPI wrapper that exposes an OpenAI-compatible `/v1/chat/completions` API.

```bash
# On the inference server (e.g. Ubuntu + Intel Arc Pro B70)
pip install openvino openvino-genai optimum[openvino] fastapi uvicorn

# Export model to OpenVINO IR format (one-time)
optimum-cli export openvino --model Qwen/Qwen3-8B --weight-format int4_sym \
    --trust-remote-code ./models/qwen3-8b-int4-ov

# Start the server using the included ov_serve.py (#299)
python server/ov_serve.py --model-dir ./models/qwen3-8b-int4-ov --port 8000

# For remote clients (multi-turn extraction), increase keep-alive timeout (#316)
python server/ov_serve.py --model-dir ./models/qwen3-8b-int4-ov --port 8000 \
    --host 0.0.0.0 --timeout-keep-alive 120
```

The included `server/ov_serve.py` provides:

- **OpenAI-compatible API** — `/v1/chat/completions`, `/v1/models`, `/health` endpoints
- **Thinking suppression** — passes `enable_thinking=False` in
  `apply_chat_template(..., extra_context={'enable_thinking': False})`,
  preventing qwen3 models from wasting ~80% of output tokens on `<think>`
  blocks. Unlike llama-server's `--reasoning-format none`, OpenVINO controls
  this at the tokenizer level.
- **Continuous batching** — queues concurrent requests and processes them in
  batches via `ContinuousBatchingPipeline` for higher aggregate throughput.
- **Prefix caching** — reuses KV cache for shared prompt prefixes across
  requests in the same batch.
- **Robust output parsing** — strips any residual `<think>` blocks before
  returning content (fence stripping is handled client-side by `llm_client.py`).
- **HTTP keep-alive** (#316) — defaults to 120-second keep-alive timeout,
  preventing TCP connection drops between sequential extraction requests.
  Configurable via `--timeout-keep-alive`.
- **Admin flush** (#361) — `POST /admin/flush` drains all queued requests,
  failing them with 503. Use this to recover from orphan requests left by
  killed extraction processes without restarting the server. In-flight
  batches complete normally. Returns `{"flushed": N, "status": "ok"}`.

Configure `config/llm.json` on the client machine:

```json
{
  "provider": "openai",
  "base_url": "http://<server-ip>:8000/v1",
  "model": "qwen3-8b-int4-ov",
  "temperature": 0.3,
  "max_tokens": 4096,
  "discovery_max_tokens": 8192,
  "pc_max_tokens": 8192,
  "timeout_seconds": 300,
  "retry_attempts": 3,
  "parallel_workers": 4,
  "skip_response_format": true,
  "context_length": 32768,
  "checkpoint_interval": 25
}
```

Key settings for OpenVINO servers:

- **`skip_response_format: true`** — OpenVINO's pipeline does not support
  `response_format={"type": "json_object"}`. The extraction pipeline parses
  JSON from freeform output anyway.
- **`parallel_workers: 4`** — Matches the server's effective batch throughput.
  The pipeline fires detail, PC, relationship, and event extraction in parallel
  after discovery completes.
- **`temperature: 0.3`** — Avoid 0.0 with qwen3 models (can cause empty
  responses or infinite thinking loops).

#### Multi-GPU Setup

For systems with multiple GPUs, run one `ov_serve.py` instance per GPU on
different ports, each pinned to a specific device:

```bash
# GPU 0 on port 8000
python server/ov_serve.py --model-dir ./models/qwen3-8b-int4-ov \
    --port 8000 --host 0.0.0.0 --device GPU.0

# GPU 1 on port 8001
python server/ov_serve.py --model-dir ./models/qwen3-8b-int4-ov \
    --port 8001 --host 0.0.0.0 --device GPU.1
```

Configure `config/llm.json` with `base_urls` (list) to enable round-robin
dispatch across both endpoints:

```json
{
  "provider": "openai",
  "base_url": "http://<server-ip>:8000/v1",
  "base_urls": [
    "http://<server-ip>:8000/v1",
    "http://<server-ip>:8001/v1"
  ],
  "parallel_workers": 4
}
```

> Other keys (model, temperature, etc.) omitted for brevity — see examples above.

- `base_url` is kept for backward compatibility and used by Ollama paths;
  `base_urls` takes precedence for the OpenAI client pool.
- When a runtime `base_url` override is provided (e.g., via `--base-url`),
  it suppresses `base_urls`, forcing single-endpoint routing.
- With `parallel_workers: 4` and 2 endpoints, each GPU receives ~2 concurrent
  requests on average, activating the server-side dynamic batching for higher
  aggregate throughput.
- Each GPU maintains its own prefix cache independently.

#### Thinking Suppression

Qwen3 models generate `<think>...</think>` blocks by default, which consume
~80% of output tokens (2000-3000 thinking tokens vs 200-500 JSON tokens).
This dramatically slows extraction and increases truncation risk.

- **llama-server**: use `--reasoning-format none` to disable thinking entirely.
- **OpenVINO**: pass `enable_thinking=False` (a Python `bool`) in the chat
  template's `extra_context` parameter when calling `apply_chat_template()`.
  The extraction pipeline's JSON parser also strips any residual `<think>`
  blocks as a safety net.

#### Fallback JSON Parser (#300)

The LLM client includes a robust JSON parser (`_parse_json_response`) that
handles non-standard model output gracefully:

1. **Normalize** — strip `<think>...</think>` blocks, extract content from
   markdown code fences, and fix malformed confidence values (e.g.
   `0-1.0` → `1.0`)
2. **Parse** — attempt `json.loads()` on the cleaned text
3. **Fallback scan** — on parse failure, use `JSONDecoder.raw_decode` to
   find the first valid `{...}` object in the output (handles cases where
   reasoning text or other preamble precedes the JSON)

This eliminates the need for `response_format` enforcement on thinking-capable
models and recovers valid JSON from outputs that include reasoning preambles
or markdown formatting.

#### Server Restart After Interrupted Extraction

If an extraction run is killed mid-flight (e.g. Ctrl+C, terminal closed),
the OpenVINO server may retain orphan requests in its batch queue. These
occupy generation slots and can cause subsequent requests to queue
indefinitely. **Restart the server process** after killing an extraction run
to flush the request queue.

#### Server Batching Behavior

The OpenVINO `ContinuousBatchingPipeline` processes requests in atomic batches:

1. Incoming requests queue until `BATCH_WAIT_MS` elapses or `MAX_BATCH_SIZE`
   requests accumulate
2. `pipeline.generate()` runs the entire batch to completion (blocking)
3. While a batch is generating, new requests queue in memory for the next batch

This means per-request throughput **decreases** as batch size grows (total VRAM
bandwidth is shared), but aggregate throughput increases:

| Batch Size | Per-request tok/s | Aggregate tok/s | Time for 8192 tokens |
|---|---|---|---|
| 1 | ~61 | ~61 | 134s |
| 2 | ~65 | ~122 | 126s |
| 4 | ~51 | ~204 | 161s |
| 8 | ~24 | ~194 | 341s |

#### Concurrency Rules

The extraction pipeline has two levels of parallelism that interact:

- **External workers** (`--workers` in `retry_failed_turns.py`): number of
  turns processed simultaneously
- **Internal workers** (`parallel_workers` in `config/llm.json`): concurrent
  LLM calls within a single turn (detail + PC + relationships + events)

Total concurrent requests = external workers × internal parallel_workers.

**Safe configurations (timeout_seconds=300):**

| External | Internal | Max Concurrent | Discovery Time (8192 tok) | Safe? |
|---|---|---|---|---|
| 1 | 4 | 4 | 144s (8192/57) | **Yes** (proven) |
| 2 | 4 | 8 | 341s (8192/24) | **No** — exceeds timeout |
| 4 | 4 | 16 | queue death | **No** — cascading failures |
| 2 | 2 | 4 | 144s | Marginal (untested) |
| 4 | 1 | 4 | 144s | Loses batching benefit |

**Rule of thumb**: Keep total concurrent requests ≤ 4 for discovery-heavy
workloads (8192 tokens). For detail-only work (4096 tokens), up to 8
concurrent is safe.

#### RTX 4070 Configuration (llama-server)

For an RTX 4070 (12 GB VRAM), use llama-server (llama.cpp) with a 14B model
at Q4 quantization. llama-server supports true parallel slot processing
(`-np 4`), unlike Ollama which queues concurrent requests on consumer GPUs.

```bash
# Start llama-server with 4 parallel slots and 8K context per slot
llama-server -m qwen2.5-14b-q4_k_m.gguf \
    -ngl 99 -np 4 -c 32768 --port 8080
```

Configure `config/llm.json`:

```json
{
  "provider": "openai",
  "base_url": "http://localhost:8080/v1",
  "model": "qwen2.5-14b",
  "temperature": 0.0,
  "max_tokens": 4096,
  "pc_max_tokens": 8192,
  "context_length": 32768,
  "timeout_seconds": 120,
  "parallel_workers": 4,
  "batch_delay_ms": 0,
  "skip_response_format": true
}
```

Key differences from the B70 config:

- **`parallel_workers: 4`** — llama-server with `-np 4` handles 4 concurrent
  requests in parallel slots (unlike Ollama which serializes them).
- **`context_length: 32768`** — Total context shared across 4 slots
  (8K effective per slot). Fits within 12 GB at Q4.
- **`batch_delay_ms: 0`** — No delay needed; the server manages slot
  scheduling internally.
- **`timeout_seconds: 120`** — Sufficient for single-slot generation speeds.

> **Why llama-server over Ollama?** Ollama wraps llama.cpp but does not expose
> parallel slot scheduling to the OpenAI-compatible endpoint. With Ollama,
> `parallel_workers: 4` in config sends 4 requests that queue serially.
> With llama-server `-np 4`, all 4 requests process simultaneously in
> dedicated KV-cache slots, achieving ~3-4× throughput for the parallel
> phases of extraction.

> **qwen3 thinking models**: If using qwen3 on llama-server, thinking mode
> cannot be fully disabled via the chat template — the server generates
> `<think>` blocks regardless. The extraction pipeline's JSON parser handles
> this automatically (strips thinking content, extracts JSON from fenced
> blocks). Use `skip_response_format: true` and `temperature: 0.3`.

#### Retrying Failed Turns

After a full extraction run, some turns may fail due to transient timeouts or
server hiccups. Use the retry tool:

```bash
# Preview what would be retried
python tools/retry_failed_turns.py --session sessions/my-session --dry-run

# Execute (sequential by default — safe for any server)
python tools/retry_failed_turns.py --session sessions/my-session
```

The tool reads `framework/extraction-log.jsonl` to identify turns where
`discovery_ok=False`, then re-extracts them. Results are merged into the
existing catalogs. The tool is idempotent — re-running it skips turns that
have since succeeded.

| Field | Description |
|---|---|
| `max_tokens` | Default max output tokens for all LLM extraction calls. |
| `pc_max_tokens` | Max output tokens for **PC entity extraction** only. Defaults to `max_tokens` if omitted. The player-character entity accumulates context over many turns and may need a higher token limit to avoid truncation. |
| `entity_refresh_interval` | Every N turns, find and re-extract stale entities whose `last_updated_turn` has fallen behind by more than N turns. Default: `50`. Set to `0` to disable. |
| `entity_refresh_batch_size` | Base number of stale entities to refresh per interval. Default: `10`. For catalogs with 60+ entities the effective batch scales to `max(batch_size, catalog_size // 5)`, capped at 25. Refresh slots are allocated proportionally by type (characters 50%, locations 20%, items 20%, factions 10%) with overflow redistribution. Entities are prioritized by staleness (most stale first) with event-frequency tiebreaking. |
| `checkpoint_interval` | Save extraction progress to disk every N turns. Default: `25`. Lower values reduce data loss on OOM interruptions at the cost of more frequent disk writes. |
| `dedup_audit_interval` | Run LLM-assisted dedup audit every N turns during extraction. Default: `50`. Set to `0` to disable. Uses an enhanced scoring prompt with narrative evolution awareness. Candidate generation currently uses string-similarity heuristics, so entities with partial name overlap are caught, but complete renames require manual coreference hints. LLM-confirmed merges bypass the name-mismatch guard. |
| `stale_item_min_refs` | Minimum number of event references for an item to survive the post-extraction stale-item sweep. Items with fewer references are removed after the staleness window expires. Default: `2`. |
| `stale_item_window` | Number of turns since `first_seen_turn` before an item becomes eligible for stale-item removal. Default: `25`. |
| `context_length` | Context window size in tokens. Passed to Ollama via `extra_body.options.num_ctx` (#175). The Modelfile variant is the primary mechanism for setting context size; this field provides a runtime override. Also used to derive the default entity context budget (25% of this value). |
| `entity_context_budget` | Optional. Explicit token budget for the known-entity section of the entity discovery prompt. When omitted, defaults to 25% of `context_length`. Recently-active entities (within the last 10 turns) get full detail; older entities are reduced to ID/name/type; entities exceeding the budget are omitted with a truncation note. Set this to a higher value if coreference quality degrades, or lower if discovery prompts are timing out. |
| `timeout_seconds` | HTTP timeout per LLM call in seconds. PC extraction uses the greater of `2×` this value and `120` seconds. |
| `retry_attempts` | Number of retries on LLM call failure. |
| `batch_delay_ms` | Delay between consecutive LLM calls in milliseconds. Prevents GPU thrashing. For cloud providers, a minimum of 2000ms is enforced automatically to avoid hitting per-minute rate limits. |
| `parallel_workers` | Number of concurrent LLM calls per turn. When set to a value greater than 1, entity detail, PC detail, relationship mapping, and event extraction calls fire concurrently after discovery completes, using a `ThreadPoolExecutor`. The inter-call delay is applied once at the end of the turn instead of between each call. Default: `1` (sequential). Set to `4` for local servers that support batched inference (e.g., OpenVINO, llama-server with `-np 4`). **Intended for local providers only** — automatically forced to `1` for cloud providers (non-localhost base URLs) to avoid triggering rate limits. |
| `consecutive_rate_limit_threshold` | Number of consecutive HTTP 429 errors before the pipeline stops to preserve quota. Default: `10`. Set higher for APIs with aggressive but transient rate limiting. |
| `ollama_options` | Optional dict of Ollama-specific parameters (e.g., `{"num_gpu": 99}`). Merged into `extra_body.options` alongside `num_ctx`. `context_length` takes precedence over `num_ctx` in this dict. |
| `ollama_format` | Ollama-only. Constrains output format via Ollama's native `format` parameter. Set to `"json"` to enforce JSON output. Distinct from the OpenAI `response_format` which hangs on qwen3.5 models. When set in combination with `ollama_think`, enables the Ollama native streaming path (`/api/chat`) instead of the OpenAI SDK. |
| `ollama_think` | Ollama-only. Boolean. Set to `false` to disable thinking mode for qwen3.5 family models, directing all `num_predict` budget to visible output. Passed as a top-level `think` parameter in the Ollama API request. |
| `skip_response_format` | Optional boolean. When `true`, omits `response_format={"type": "json_object"}` from OpenAI SDK calls. Auto-enabled for Ollama + qwen3.5 models to avoid hangs. Set explicitly when using other models or providers that don't support `response_format`. |

**PC extraction cooldown:** If PC extraction fails for 20 consecutive turns (`_PC_SKIP_THRESHOLD`), it enters a cooldown cycle: skipping 50 turns, then retrying for 5 turns, repeating until a success resets the counter (#133, #168). An end-of-run summary reports how many turns were skipped.

### PC Entity Extraction - Context Constraints

Player-character extraction uses a trimmed prior-context payload to stay within
effective context limits on long campaigns. The allowlist is defined in
`tools/semantic_extraction.py` as `_PC_KEY_STABLE_ATTRS` and currently includes:

- `species`
- `race`
- `class`
- `aliases`

This list is intentionally small. Many PC-facing fields (for example: level,
background, equipment, condition, quest, and status) are excluded from prior
context because they are volatile and increase token pressure in late-turn
extraction.

If you need to preserve and extract additional PC attributes more reliably,
use segmented extraction (`--segment-size`) to reduce late-turn context bloat,
then revisit the allowlist size.

Or use CLI overrides for one-off runs:

```bash
python tools/bootstrap_session.py \
    --session sessions/my-session \
    --file transcript.txt \
    --framework framework-local \
    --model qwen2.5:14b \
    --base-url http://localhost:11434/v1
```

**Note:** `--model` and `--base-url` override only those settings. The current implementation still reads `api_key_env` from `config/llm.json` (by default this is often `OPENAI_API_KEY`). If your local OpenAI-compatible server does not require an API key, either set the expected environment variable anyway or set `"api_key_env": ""` in `config/llm.json` before running the command.

---

## Ingesting Turns

### Adding a DM Response

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "You arrive at the village of Thornhaven at dusk. The streets are empty, and every door is shut tight. A crow watches you from the inn's sign."
```

### Adding a Player Prompt

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker player \
  --text "I approach the inn and knock on the door."
```

The script will:
- Assign a turn ID and sequence number
- Create `sessions/session-001/transcript/turn-NNN-{speaker}.md`
- Append to `sessions/session-001/raw/full-transcript.md`

To also run LLM-based semantic extraction on the new turn:

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "..." \
  --extract
```

### Bootstrapping an Existing Transcript

If you already have a large transcript file, bootstrap a session in one pass:

Place the raw source text inside the session's `raw/` directory:

> **Warning:** `sessions/session-001/` is tracked as the public example, so
> placing a real (private) transcript under `sessions/session-001/raw/` risks
> committing private content. Use a gitignored session directory instead — e.g.
> `sessions/session-import/` (already listed in `.gitignore`) — or add your
> chosen `sessions/<session>/` path to `.gitignore` before placing transcripts
> under `sessions/<session>/raw/`.

```bash
mkdir -p sessions/session-import/raw
# Place your transcript at:
# sessions/session-import/raw/full-transcript.md
```

```bash
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md
```

Useful flags:
- `--dry-run` preview parsed turns and files before writing
- `--format {auto,markdown,labeled,alternating}` override auto-detect
- `--dm-label` / `--player-label` for non-default speaker labels

**Important:** The `--player-label` must match the speaker label text used in your source transcript (case-insensitive; pass the label text without the trailing colon). For example, if the transcript uses `Fenouille Moonwind:` as the player label, pass `--player-label "Fenouille Moonwind"`. If the label text doesn't match, the parser won't recognize player turns and their text will be appended to the preceding DM turn, contaminating extraction input.

---

## Updating State

After ingesting new turns, update the derived state:

```bash
python tools/update_state.py --session sessions/session-001
```

This regenerates:
- `sessions/session-001/derived/turn-summary.md`
- `sessions/session-001/derived/state.json`
- `sessions/session-001/derived/objectives.json`
- `sessions/session-001/derived/evidence.json`

### Planning Layer Derivation

When catalog data is available (from semantic extraction), pass `--framework` to
populate derived planning files from catalog entities, events, and timelines:

```bash
python tools/update_state.py --session sessions/session-001 --framework framework/
```

This additionally populates:
- `state.json` — world state from location summaries, player state from the player
  entity's volatile state (location, condition, equipment, relationships), known/inferred
  constraints from entity attributes, risks from adversarial relationships, opportunities
  from active plot thread open questions, active threads from plot-threads.json
- `evidence.json` — explicit evidence from catalog events, inferences from entity
  attributes with `inference: true`, inferred relationship evidence from low-confidence
  relationships
- `timeline.json` — merged session-level (pattern-extracted) and catalog-level temporal
  markers, deduplicated and sorted by turn number

Placeholder values (e.g. `TODO:`, `Unknown`) are replaced; manually authored content
is preserved. Evidence entries are deduplicated — running the tool multiple times is safe.

The derivation tool can also be run standalone:

```bash
python tools/derive_planning_layer.py --session sessions/session-001 --framework framework/
```

### Limitations

`update_state.py` does **not** currently update:
- `framework/story/*`
- `framework/dm-profile/dm-profile.json`

Those updates are currently manual/Copilot-assisted.

For automated catalog updates, see Semantic Extraction below.

---

## Semantic Extraction (Optional)

The semantic extraction pipeline uses an LLM to automatically extract entities, relationships, and events from transcript turns and merge them into `framework/catalogs/`.

### Setup

1. Install the optional LLM dependency:
   ```bash
   pip install -r requirements-llm.txt
   ```

2. Configure `config/llm.json` (recommended: llama-server):
   ```json
   {
     "provider": "openai",
     "base_url": "http://localhost:8080/v1",
     "model": "Qwen3.5-9B-Q4_K_M",
     "api_key_env": ""
   }
   ```

   See [Using a Local Model](#using-a-local-model) for llama-server launch commands
   and [Using Ollama](#using-ollama) if you prefer Ollama.

   For a cloud provider (e.g. OpenAI):
   ```json
   {
     "provider": "openai",
     "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o",
     "api_key_env": "OPENAI_API_KEY"
   }
   ```

### Batch Mode (Bootstrap)

When bootstrapping a session, semantic extraction runs automatically over all turns if the LLM is configured:

```bash
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md
```

The pipeline processes each turn through four agents:
1. **Entity Discovery** — identify entities mentioned in the turn
2. **Entity Detail Extractor** — extract/update attributes per entity
3. **Relationship Mapper** — identify cross-entity relationships
4. **Event Extractor** — identify narrative events

Progress is checkpointed every `checkpoint_interval` turns (default 25, configurable in `config/llm.json`) and can resume after interruption. The progress file is stored at `<framework_dir>/extraction-progress.json` (e.g. `framework/extraction-progress.json`).

To force a fresh extraction that ignores any saved progress and extraction log state, use the `--no-resume` flag:

```bash
python tools/bootstrap_session.py --session sessions/session-001 \
  --file transcript.txt --no-resume
```

This is useful when prior extraction state is stale or corrupted and you want to re-extract all turns from scratch.

### Detached Batch Execution (Recommended)

For long extraction runs, launch extraction in a detached process so work in
other VS Code chat sessions does not affect the running job.

Use the helper script from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_extraction_detached.ps1 `
  -Session sessions/session-import `
  -TranscriptFile sessions/session-import/raw/full-transcript.md `
  -Framework framework-local `
  -PlayerLabel "Fenouille Moonwind" `
  -SegmentSize 100
```

The helper safely quotes argument values passed to `Start-Process`, so values
with spaces (for example `-PlayerLabel "Fenouille Moonwind"`) are passed as a
single argument to `bootstrap_session.py`.

The script writes:
- stdout log: `run-logs/extract-<timestamp>.log`
- stderr log: `run-logs/extract-<timestamp>.err.log`
- PID file: `run-logs/extract-<timestamp>.pid`
- command helper: `run-logs/extract-<timestamp>.cmd.txt`

Optional detached-launch flags include `-Model`, `-Framework`,
`-PlayerLabel`, and `-Overwrite`.

Monitor/status a detached run (latest run by default):

```powershell
powershell -ExecutionPolicy Bypass -File tools/watch_extraction_detached.ps1
```

Follow live stdout for a specific run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/watch_extraction_detached.ps1 `
  -PidFile run-logs/extract-<timestamp>.pid `
  -Follow
```

Manual monitoring (without helper script):

```powershell
Get-Content run-logs/extract-<timestamp>.log -Tail 80
Get-Content run-logs/extract-<timestamp>.err.log -Tail 80
Get-Process -Id (Get-Content run-logs/extract-<timestamp>.pid)
```

Stop a detached run (latest run by default):

```powershell
powershell -ExecutionPolicy Bypass -File tools/stop_extraction_detached.ps1
```

Stop a specific run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/stop_extraction_detached.ps1 `
  -PidFile run-logs/extract-<timestamp>.pid
```

Manual stop (without helper script):

```powershell
Stop-Process -Id (Get-Content run-logs/extract-<timestamp>.pid)
```

### Pre-flight Context Window Check

Before extraction begins, the pipeline estimates whether the configured
`context_length` can sustain extraction for the session. This catches
misconfigured runs (e.g., an 8K context window for a 300+ turn session) before
committing to a multi-hour extraction.

The check estimates peak context usage based on:
- Number of turns remaining to process
- Projected entity growth (~0.4 new entities per turn)
- Existing entity count (when resuming from a checkpoint)
- Output token reservation (`max_tokens`)
- System prompt template overhead

**Example warning output:**
```
  === Pre-flight Context Check (model: qwen2.5:14b) ===
  WARNING: Estimated peak usage (10,916 tokens) exceeds context window (8,192 tokens) by 2,724 tokens.
  Suggestions:
    - Increase context_length in config/llm.json (32K+ recommended for sessions over 100 turns).
    - Enable segmented extraction (--segment-size 100) to limit entity accumulation per segment.
  The extraction will proceed, but quality may degrade as context fills up.
```

The check is a **warning only** — extraction proceeds regardless so advanced
users can run with tight configurations intentionally. To resolve warnings:

1. **Increase `context_length`** in `config/llm.json` to match your model's
   actual context window (32K+ recommended for sessions over 100 turns)
2. **Enable segmented extraction** with `--segment-size 100` to reset entity
   accumulation per segment
3. **Use a model with a larger context window** for very large sessions

When segmented extraction is enabled, the check accounts for the segment size
rather than the total turn count, since entities only accumulate within each
segment.

### Segmented Extraction

For sessions with more than 150 turns on models with <=32K context windows,
use segmented extraction to prevent quality degradation in late turns:

Explicit segmented extraction command:

```bash
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md \
  --segment-size 100
```

Each segment processes turns with a fresh entity catalog. After all segments
complete, entities are automatically reconciled across segment boundaries by
ID and name matching.

As of issue #197, when `--segment-size` is omitted the bootstrap tool
automatically applies `--segment-size 100` for sessions larger than 150 turns.
Pass `--segment-size 0` explicitly to disable segmentation.

Equivalent command relying on the auto-default (>150 turns):

```bash
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md
```

Recommended segment sizes:
- 7B models (8K context): 50 turns
- 14B models (32K context): 100-150 turns
- 70B+ models (128K context): 300+ turns (may not need segmentation)

Segment size 0 runs a single-pass extraction without segmentation.

### Incremental Extraction Workflow

For large sessions, extract in small batches with human review between each
increment. This gives you control over quality before committing to the next
batch.

#### Flags

- `--start-turn N` — Start extraction from turn N (1-based). Turns before N
  are skipped; existing catalogs are used as prior context.
- When both `--start-turn` and `--max-turns` are set, `--max-turns` is treated
  as an **absolute turn number** (upper bound), not a count.
  `--start-turn 26 --max-turns 50` extracts turns 26–50.

#### Typical 3-batch workflow

```bash
# Batch 1: turns 1-25
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md \
  --max-turns 25

# Review wiki pages in framework/catalogs/*/README.md
# If satisfied, continue:

# Batch 2: turns 26-50
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md \
  --start-turn 26 --max-turns 50

# Review again, then:

# Batch 3: turns 51-75
python tools/bootstrap_session.py \
  --session sessions/session-import \
  --file sessions/session-import/raw/full-transcript.md \
  --start-turn 51 --max-turns 75
```

After each batch the tool prints a suggested `--start-turn` / `--max-turns`
command for the next increment.

Wiki pages are auto-generated after each extraction so you can review entity
pages before continuing.

#### `discovery_temperature` config key

Entity discovery can benefit from a slightly higher temperature to catch
entities the model might otherwise miss. Add `discovery_temperature` to
`config/llm.json` to override the global temperature for the discovery phase
only:

```json
{
  "temperature": 0.0,
  "discovery_temperature": 0.3
}
```

When omitted, discovery uses the global `temperature` value.

### Coreference Hints

When the automatic dedup pass doesn't catch all duplicates (e.g., a character
referred to by descriptions like "broad figure" before being named), you can
provide a manual hints file to force deterministic merging.

Create `sessions/<session>/coreference-hints.json`:

```json
{
  "character_groups": [
    {
      "canonical_name": "Kael",
      "canonical_id": "char-kael",
      "variant_names": ["broad figure", "young hunter", "brave warrior"],
      "variant_id_patterns": ["char-broad-figure", "char-young-hunter", "char-brave-warrior"],
      "notes": "Kael appears unnamed before turn-149"
    }
  ]
}
```

Fields:
- `canonical_name` / `canonical_id` — the entity that survives the merge
- `variant_names` — entity names to match (case-insensitive)
- `variant_id_patterns` — entity IDs to match exactly
- `notes` — optional documentation

The hints file is validated against `schemas/coreference-hints.schema.json`.
If no hints file exists, the pipeline skips this step gracefully.

During batch extraction, coreference hints run after the automatic dedup pass.
Variant entities are merged into the canonical entity: relationships, events,
and stable attributes are absorbed, variant files are deleted from disk, and
all dangling references are rewritten.

### Post-Extraction Dedup

After extraction completes, run the dedup audit to identify and merge duplicate entities:

```bash
# Generate candidates, score with LLM, write review file (safe — no changes)
python tools/dedup_audit.py

# Also auto-merge high-confidence pairs (>=0.9)
python tools/dedup_audit.py --auto-merge

# Custom review file location
python tools/dedup_audit.py --review-file path/to/review.json
```

Review `dedup-review.json` for medium-confidence pairs. Set `"action": "merge"` or `"action": "keep_separate"` for each entry, then apply approved merges:

```bash
python tools/dedup_audit.py --apply-review
```

### Incremental Mode (Ingest)

Pass `--extract` to `ingest_turn.py` to run semantic extraction on a single new turn:

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "The elder reveals that the crystal was shattered decades ago." \
  --extract
```

See [`docs/semantic-extraction-design.md`](semantic-extraction-design.md) for full pipeline design.

### Extraction Log

During extraction (batch, segmented, or single-turn), the pipeline writes a per-turn log to `<framework-dir>/extraction-log.jsonl`. Each line is a JSON object recording:

- `turn_id` — which turn was processed
- `timestamp` — UTC wall-clock time
- `discovery_ok`, `detail_ok`, `pc_ok`, `relationships_ok`, `events_ok` — per-phase success flags
- `*_error` — error message when a phase failed (null on success)
- `new_entities`, `new_events` — counts of entities/events added by this turn
- `discovery_proposals` — array of all entities proposed by the model for this turn, each with `name`, `is_new`, `proposed_id`, `existing_id`, and `confidence`
- `discovery_filtered` — array of entities rejected during filtering, each with `name`, `id`, and `reason` (`below_confidence_threshold`, `concept_prefix`, or `compound_term_fragment`). `compound_term_fragment` indicates the entity's single-word name was found in the runtime compound-term index built from multi-word entity names in the catalog and current turn (e.g., "quiet" rejected because "Quiet Weave" exists).
- `elapsed_ms` — wall-clock time for the turn

The file is append-only and survives interruptions. To diagnose a failed run:

```bash
# Show all turns where any phase failed
python -c "
import json
phases = ('discovery', 'detail', 'pc', 'relationships', 'events')
for rec in (json.loads(line) for line in open('framework/extraction-log.jsonl')):
    failed = [p for p in phases if rec.get(p + '_ok') is False]
    if failed:
        errors = '; '.join(f'{p}: {rec.get(p + \"_error\") or \"failed\"}' for p in failed)
        print(f'{rec[\"turn_id\"]}: {errors}')
"
```

The log is not written in `--dry-run` mode.

---

## Generating Next-Move Analysis

```bash
python tools/analyze_next_move.py --session sessions/session-001
```

This generates:
- `sessions/session-001/derived/next-move-analysis.md` — situation analysis
- `sessions/session-001/derived/prompt-candidates.json` — candidate prompts

### Prompt Generation Modes

| Mode | Description |
|---|---|
| `desired_outcome` | Prompts optimized for achieving player objectives (default) |
| `roleplay_consistent` | Prompts that stay in character |
| `all_options` | A broad set covering safe, probing, and aggressive approaches |

To request a specific mode:

```bash
python tools/analyze_next_move.py --session sessions/session-001 --mode all_options
```

---

## DM Profile Analysis

The DM profile tool populates `framework/dm-profile/dm-profile.json` with behavioral patterns inferred from the transcript and/or user-provided off-game documents.

### Transcript Analysis (LLM-based)

Analyze all DM turns from a session:

```bash
python tools/dm_profile_analyzer.py --session sessions/session-001
```

Analyze a specific range of turns:

```bash
python tools/dm_profile_analyzer.py --session sessions/session-001 --start-turn 20 --max-turns 30
```

The tool sends batches of DM turns (default 5 per LLM call) to the model with the `templates/extraction/dm-profile-analyzer.md` prompt. Extracted observations cover:

- **Tone** — narrative voice and mood (e.g. "dark and atmospheric", "lighthearted")
- **Structure patterns** — how the DM organizes responses (paragraph count, dialogue separation)
- **Hint patterns** — how clues are delivered (embedded in descriptions, direct, misleading)
- **Adversarial level** — how challenging or punishing the DM is (low/moderate/high)
- **Formatting preferences** — second-person narration, dialogue markers, emphasis

### User-Provided Input

For off-game knowledge the transcript can't reveal, fill in the template and pass it:

```bash
cp templates/content/dm-profile-user-input.md my-dm-notes.md
# Edit my-dm-notes.md with your DM knowledge
python tools/dm_profile_analyzer.py --user-input my-dm-notes.md
```

Both sources can be combined in one invocation:

```bash
python tools/dm_profile_analyzer.py --session sessions/session-001 --user-input my-dm-notes.md
```

### Automatic Integration

- **Bootstrap**: `bootstrap_session.py` automatically runs DM profile analysis after semantic extraction.
- **Incremental**: `ingest_turn.py --extract` updates the DM profile for each new DM turn.
- **Analysis**: `analyze_next_move.py` includes the DM profile summary in the analysis output when `--framework` is specified.

### Auto-Resume Behavior

By default the tool **automatically resumes** from where it left off. When `--start-turn` is not specified, it reads `last_updated_turn` from the existing profile and starts from the next turn. This makes interrupted or incremental runs efficient — already-analyzed turns are never re-processed.

```bash
# First run: analyzes turns 1–50, profile saved with last_updated_turn = turn-050
python tools/dm_profile_analyzer.py --session sessions/session-001

# Second run: automatically resumes from turn-051
python tools/dm_profile_analyzer.py --session sessions/session-001
```

To force a **full reanalysis** from the beginning, either pass `--start-turn 1` or delete the profile file:

```bash
# Force full reanalysis using --start-turn
python tools/dm_profile_analyzer.py --session sessions/session-001 --start-turn 1

# Or delete the profile to reset
rm framework/dm-profile/dm-profile.json
python tools/dm_profile_analyzer.py --session sessions/session-001
```

> **Note on partial failures**: if a batch fails mid-run (LLM extraction error), the watermark advances only through the last *consecutively* successful batch from the start. This prevents silently skipping failed turns on the next run — the failed range will be retried automatically on resume.

### Confidence Scores

- Observations from single turns get lower confidence (0.3–0.5)
- Corroborated patterns across multiple turns get higher confidence (0.6–0.9)
- Confidence is capped at 0.9; 1.0 is reserved for user-confirmed patterns
- User-provided input sets a minimum confidence of 0.3
- Profile confidence never regresses — new data can only raise or maintain it

---

## Timeline Configuration

The pipeline tracks in-game time progression by extracting temporal signals (season keywords, biological markers, construction milestones, time-skip language) from transcript turns. By default, turn-001 is Day 0.

Timeline data is stored in `framework/catalogs/timeline.json` and conforms to `schemas/timeline.schema.json`.

### Reference Anchor

The reference anchor defaults to turn-001 = Day 0. To use a custom anchor, pass a `timeline_anchor` dict when calling `synthesize_entity()` or `assemble_character_page()`:

```python
anchor = {
    "turn": "turn-292",
    "label": "Foundation of the Quiet Weave",
    "day": 0
}
```

A future release will support loading the anchor from `config/llm.json` automatically.

Events before the anchor receive negative day estimates; events after receive positive.

### Season Granularity

The timeline uses 12 fine-grained season labels: `early_winter`, `mid_winter`, `late_winter`, `early_spring`, `mid_spring`, `late_spring`, `early_summer`, `mid_summer`, `late_summer`, `early_autumn`, `mid_autumn`, `late_autumn`.

### Day Estimation

Day offsets are estimated using a configurable days-per-turn ratio (default: 3.5). Confidence scores decrease with distance from the anchor. For campaigns with variable pacing, day estimates serve as rough approximations.

### Wiki Integration

When timeline data is available, wiki pages include:
- **Infobox**: "First Seen Day" with estimated day and season label
- **Event Timeline**: An "Est. Day" column showing approximate in-game day for each event
- **Timeline page** (`framework/catalogs/timeline.md`): A narrative timeline wiki page with current position, prose temporal summary, and reference tables

### Timeline Wiki Page

The timeline wiki page is generated automatically alongside entity pages:

```bash
# Generate all wiki pages including timeline
python tools/generate_wiki_pages.py --framework framework-local/

# Generate only the timeline page
python tools/generate_wiki_pages.py --framework framework-local/ --type timeline
```

The page contains:
1. **Current Position** — infobox with estimated day, season, anchor event, and confidence
2. **Narrative Summary** — structured story progression using catalog events (or concise fallback when no catalog data available)
3. **Season Progression** — table of confirmed season transitions (flicker-filtered)
4. **Time Passages** — table of detected time skips
5. **Biological & Lifecycle Markers** — pregnancies, births, and other lifecycle events
6. **Other Milestones** — construction and anchor events

When `events.json` is available in the catalog directory, the narrative summary uses event
descriptions to produce a richer story progression grouped by temporal period. Without events,
a concise 3-sentence fallback is produced (elapsed time, season arc, time passage count).

### Season Flicker Filtering

Low-confidence season signals (regex false positives such as "harvest" in a winter story) are automatically filtered. A season transition is kept only if:
- Its confidence ≥ 0.6 (high-quality signal), OR
- At least 1 neighboring season entry within a sliding window of 5 entries on each side shares the same base season (winter/spring/summer/autumn)

Additionally, base season detection requires at least 2 distinct keyword matches and a margin of 2 over the runner-up, preventing single occurrences of common words ("cold", "warm", "fall") from triggering false detections. Signal text is capped at 120 characters to avoid storing full paragraphs from greedy matches.

### Timeline Wiki Page

A dedicated timeline overview page is generated at `framework/catalogs/timeline.md` alongside the entity wiki pages. It provides a summarized, human-readable view of all temporal data:

- **Season Progression**: Groups consecutive same-season entries into ranges (e.g., "Turns 3–25: Mid Winter") rather than listing each individually
- **Time Skips**: Notable time jumps with descriptions and confidence scores
- **Biological Markers**: Sleep/wake cycles, meals, and rest periods
- **Day Progression**: Estimated day offsets for entries with day data
- **Other Temporal Markers**: Anchor events, construction milestones, explicit dates

Generate it with:
```bash
# Generate all wiki pages including timeline
python tools/generate_wiki_pages.py --framework framework/

# Generate only the timeline page
python tools/generate_wiki_pages.py --framework framework/ --type timeline
```

---

## Story Summary

After extraction, generate a high-level narrative arc summary:

```bash
# Generate story summary using configured LLM
python tools/generate_story_summary.py --framework framework/

# Generate data-only summary (no LLM required)
python tools/generate_story_summary.py --framework framework/ --no-llm
```

The summary is written to `framework/story/summary.md` and includes:
- **Arc Overview** — narrative summary of the campaign's major arcs, character journey, and current state
- **Open Questions** — unresolved questions from active and dormant plot threads

In LLM mode, the tool assembles a structured prompt from events, plot threads, entity catalogs, and timeline data, then calls the configured model. If the LLM call fails, it automatically falls back to data-only mode.

The data-only mode produces a structured markdown overview without LLM calls, covering campaign scope, player character status, plot thread status (active/dormant/resolved), and key events.

---

## Validating JSON

```bash
# Validate a specific session
python tools/validate.py --session sessions/session-001

# Validate the framework
python tools/validate.py --framework framework

# Validate everything
python tools/validate.py --all
```

---

## Building the Scene Graph

The scene graph is a cross-type spatial and temporal index built from existing entity catalogs. It enables fast scene-resolution queries without scanning every entity file.

```bash
# Build from framework catalogs
python tools/build_scene_graph.py --framework framework/

# Custom output path
python tools/build_scene_graph.py --framework framework/ --output path/to/scene-graph.json
```

The scene graph is used automatically by `build_context.py` for nearby-entity lookups. If the scene graph file is absent, `build_context.py` falls back to the original full-catalog scan. To disable scene graph usage explicitly:

```bash
python tools/build_context.py --session sessions/session-001 --turn turn-078 --framework framework/ --no-scene-graph
```

Rebuild the scene graph after extraction runs or catalog updates to keep the index current.

---

## MCP Tools

The repository includes lightweight MCP (Model Context Protocol) servers that provide utility tools for VS Code Copilot agents.

### Setup

Install the MCP dependency:

```bash
pip install -r requirements-mcp.txt
```

MCP servers are registered in `.vscode/mcp.json` and discovered automatically by VS Code.

### Wait Tool

The `wait` tool lets the coordinator agent pause for a specified duration before resuming work, enabling token-efficient monitoring of long-running processes.

**Tool:** `wait(seconds, message)`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `seconds` | int | Yes | Duration to wait (1–14400, max 4 hours) |
| `message` | string | No | Description of what you're waiting for |

**Returns:** Confirmation with actual elapsed time.

**Example usage pattern** (coordinator agent):
1. Launch a detached extraction run
2. Estimate remaining time (e.g. 2 hours)
3. Call `wait(seconds=5760, message="extraction run ~80% of estimated 2h")`
4. After wait completes, dispatch a status check subagent

**Error handling:**
- Rejects `seconds < 1` or `seconds > 14400`
- Rejects non-integer values

---

## Using with VS Code Copilot

Open the repo in VS Code with GitHub Copilot enabled.

Copilot will follow the instructions in `.github/copilot-instructions.md` automatically.

### Recommended Copilot Prompts

**After adding a DM turn:**
```
Update state.json, objectives.json, and evidence.json based on the latest DM turn in the transcript.
```

**To generate analysis:**
```
Generate next-move-analysis.md and prompt-candidates.json for the current session state.
```

**To explore entities:**
```
What do we know about [character/location] from the transcript? Separate explicit evidence from inference.
```

**To check objectives:**
```
Which objectives are most at risk based on the current state? What actions would advance them?
```

**To probe DM patterns:**
```
Based on the DM profile, what hints might be bait vs. genuine? Update dm_bait evidence accordingly.
```

---

## File Editing Guidelines

### Raw Transcript Files

**Never edit.** Only append new turns using `ingest_turn.py`.

### Derived Files

Safe to edit manually or via Copilot. `update_state.py` regenerates `turn-summary.md` and ensures scaffold files exist.

### Catalog Files

Append only. Do not delete or rename existing entries — use `last_updated_turn` to track changes.

### Schema Files

Do not edit unless the data model needs to change. If schemas change, re-validate all data files.

---

## Post-Extraction Validation

After running semantic extraction, validate the output against curated ground truth to catch entity-level problems that schema validation alone cannot detect (false alias merges, missing characters, coreference fragmentation, entity staleness).

### Running

```bash
# Default: validates framework-local/catalogs against full-session ground truth
python tools/validate_extraction.py --catalog-dir framework-local/catalogs

# Custom ground truth fixture
python tools/validate_extraction.py --catalog-dir framework-local/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
```

### Interpreting the Scorecard

The script checks 7 categories and prints a scorecard with PASS/WARN/FAIL for each item:

| Category | What it checks |
|---|---|
| Independent Characters | Expected NPCs exist as separate catalog entities |
| PC Aliases | Only legitimate aliases appear on char-player |
| Must-Not-Merge | Named entities were not incorrectly absorbed into other entities |
| Coreference Groups | Pre-naming descriptions merged into the canonical entity |
| Staleness | Entity `last_updated_turn` is within expected range |
| Locations (late-game) | Expected late-game locations exist in catalogs |
| Factions (late-game) | Expected late-game factions exist in catalogs |

Exit code 0 means no FAILs; exit code 1 means at least one FAIL was found.

### Ground Truth Fixtures

Ground truth files live in `tests/fixtures/` and define the expected extraction output for a session or turn range. The full-session fixture (`extraction-ground-truth-full-session.json`) covers turns 1–345 of `session-import` and is derived from `docs/design-synthesis-layer.md`.

---

## Post-Extraction Recovery

If post-processing bugs are fixed after an extraction run has completed, use `recover_postprocess.py` to re-apply the corrected passes without re-extracting. This avoids repeating expensive LLM calls when only the post-processing logic has changed.

### Running

```bash
# Preview changes without writing
python tools/recover_postprocess.py --catalog-dir framework-local/catalogs --dry-run

# Apply recovery passes and save
python tools/recover_postprocess.py --catalog-dir framework-local/catalogs
```

### What it does

The script runs 5 passes in order:

1. **Strip false-positive PC aliases** — removes blocklisted words, NPC names, and title-prefixed aliases from `char-player`
2. **Fix empty `first_seen_turn`** — backfills from event data or `last_updated_turn`
3. **Catalog dedup** — merges duplicate entities and rewrites stale IDs in relationships/events
4. **Relationship cleanup + dedup** — removes dangling relationships and consolidates duplicates
5. **PC alias merge** — re-runs the tightened `_merge_pc_aliases()` heuristics

Both catalogs and events are saved to disk after recovery.

---

## Example Session

See `examples/demo-session/` for a complete worked example with 6 turns, 3 entities, 2 plot threads, and annotated analysis.

---

## Heartbeat Wrapper for Long-Running Extraction

### Problem

When an AI agent session launches a long extraction batch (15–25 minutes) via `run_in_terminal mode=async`, VS Code's idle detection considers the terminal "finished" as soon as output stops for >1 second. The agent then resorts to hundreds of polling calls to detect completion, wasting context window budget.

### Solution

The heartbeat wrapper scripts print a `.` character every 500ms to stderr to keep the terminal "alive" until the wrapped command finishes. Dots go to stderr so they don't corrupt structured stdout output (VS Code idle detection watches all terminal output, not just stdout). When the command exits, the heartbeat stops, the terminal goes idle (>1000ms silence), and the agent receives an automatic completion notification. This turns O(n) polling into exactly 1 async call.

### Usage

**Python (cross-platform, recommended):**
```bash
python tools/run_with_heartbeat.py python tools/bootstrap_session.py --session sessions/session-001 --all
```

**Bash (Linux/macOS/WSL):**
```bash
# Ensure executable permission on fresh clones:
chmod +x tools/run_with_heartbeat.sh

./tools/run_with_heartbeat.sh python tools/bootstrap_session.py --session sessions/session-001 --all

# Or invoke directly with bash (no chmod needed):
bash tools/run_with_heartbeat.sh python tools/bootstrap_session.py --session sessions/session-001 --all
```

**PowerShell (Windows):**
```powershell
.\tools\run_with_heartbeat.ps1 -Command "python tools/bootstrap_session.py --session sessions/session-001 --all"
```

### With `run_in_terminal mode=async`

In a Copilot agent session, wrap the extraction command:

```
run_in_terminal mode=async:
  python tools/run_with_heartbeat.py python tools/bootstrap_session.py --session sessions/session-001 --all
```

The agent will receive automatic notification when extraction completes, with no polling required.

### Exit Code Propagation

All wrapper variants propagate the wrapped command's exit code. If the extraction fails with exit code 1, the wrapper also exits with code 1.
