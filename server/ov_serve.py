"""
OpenVINO GenAI REST Server — OpenAI-compatible chat completions endpoint.

Uses ContinuousBatchingPipeline with prefix caching and dynamic batching
for high-throughput extraction workloads on Intel Arc Pro B70.

Dynamic batching: incoming requests are collected for up to BATCH_WAIT_MS,
then processed together in a single pipeline.generate() call for maximum
GPU utilization.

Endpoints:
  POST /v1/chat/completions  — OpenAI-compatible chat completions
  GET  /v1/models            — List available models
  GET  /health               — Health check

Usage:
  pip install fastapi uvicorn openvino-genai
  python ov_serve.py [--port 8000] [--model-dir /path/to/model]
"""
import argparse
import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

import openvino_genai as ov_genai
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# --- Configuration (overridden by CLI args / env vars) ---
MODEL_DIR = os.environ.get("OV_MODEL_DIR", "")
CACHE_DIR = ""
MODEL_NAME = ""
BATCH_WAIT_MS = 50  # Max time to wait for batch to fill
MAX_BATCH_SIZE = 8   # Max requests per batch
CACHE_SIZE_GB = 8    # KV cache size in GB
REQUEST_TIMEOUT_S = 600  # Per-request timeout (seconds); 0 = no timeout
EXTRA_STOP_TOKEN_IDS: set = set()  # Additional stop token IDs beyond EOS

# --- Request/Response Models ---

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    max_tokens: Optional[int] = Field(default=2048, alias="max_tokens")
    temperature: Optional[float] = 0.0
    top_p: Optional[float] = 1.0
    stop: Optional[list[str]] = None
    n: Optional[int] = 1

class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str

class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageInfo

# --- Global pipeline ---
pipeline = None
tokenizer = None

# --- Dynamic Batching Queue ---
@dataclass
class BatchRequest:
    prompt: str
    gen_config: object
    future: asyncio.Future = field(default=None)

batch_queue: asyncio.Queue = None
batch_worker_task = None

async def batch_worker():
    """Collect requests into batches and process them together.

    Includes a per-batch timeout watchdog: if pipeline.generate() doesn't
    return within REQUEST_TIMEOUT_S, all requests in the batch are failed
    with a timeout error and the pipeline is reloaded to clear the stuck
    state.
    """
    global pipeline, tokenizer
    loop = asyncio.get_running_loop()
    while True:
        # Wait for at least one request
        first = await batch_queue.get()
        batch = [first]

        # Collect more requests for up to BATCH_WAIT_MS
        deadline = loop.time() + BATCH_WAIT_MS / 1000.0
        while len(batch) < MAX_BATCH_SIZE:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(batch_queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break

        # Process batch with timeout protection
        prompts = [r.prompt for r in batch]
        gen_configs = [r.gen_config for r in batch]

        start = time.perf_counter()
        try:
            timeout = REQUEST_TIMEOUT_S if REQUEST_TIMEOUT_S > 0 else None
            results = await asyncio.wait_for(
                loop.run_in_executor(None, pipeline.generate, prompts, gen_configs),
                timeout=timeout,
            )
            elapsed = time.perf_counter() - start

            total_tokens = 0
            for i, (req, result) in enumerate(zip(batch, results)):
                # m_generation_ids is the public output field in openvino_genai
                output_text = result.m_generation_ids[0]
                total_tokens += count_tokens(output_text)
                req.future.set_result((output_text, elapsed))

            tok_s = total_tokens / elapsed if elapsed > 0 else 0
            print(f"[batch={len(batch)}] {total_tokens} tok in {elapsed:.2f}s ({tok_s:.1f} tok/s aggregate)")
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - start
            print(f"[WATCHDOG] Batch of {len(batch)} request(s) timed out after {elapsed:.0f}s — reloading pipeline")
            for req in batch:
                if not req.future.done():
                    req.future.set_exception(
                        HTTPException(status_code=504, detail=f"Generation timed out after {elapsed:.0f}s")
                    )
            # Reload pipeline to clear stuck state
            await _reload_pipeline()
        except Exception as e:
            for req in batch:
                if not req.future.done():
                    req.future.set_exception(e)


async def _reload_pipeline():
    """Reload the model pipeline to recover from a stuck state."""
    global pipeline, tokenizer
    print("[WATCHDOG] Reloading model pipeline...")
    start = time.perf_counter()
    loop = asyncio.get_running_loop()
    try:
        sched_cfg = ov_genai.SchedulerConfig()
        sched_cfg.cache_size = CACHE_SIZE_GB
        sched_cfg.enable_prefix_caching = True
        pipeline = await loop.run_in_executor(
            None,
            lambda: ov_genai.ContinuousBatchingPipeline(
                MODEL_DIR, sched_cfg, 'GPU', {'CACHE_DIR': CACHE_DIR}
            ),
        )
        tokenizer = pipeline.get_tokenizer()
        elapsed = time.perf_counter() - start
        print(f"[WATCHDOG] Pipeline reloaded in {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"[WATCHDOG] Pipeline reload FAILED after {elapsed:.1f}s: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, tokenizer, batch_queue, batch_worker_task
    print(f"Loading model from {MODEL_DIR}...")
    start = time.perf_counter()

    sched_cfg = ov_genai.SchedulerConfig()
    sched_cfg.cache_size = CACHE_SIZE_GB
    sched_cfg.enable_prefix_caching = True

    pipeline = ov_genai.ContinuousBatchingPipeline(
        MODEL_DIR, sched_cfg, 'GPU', {'CACHE_DIR': CACHE_DIR}
    )
    tokenizer = pipeline.get_tokenizer()

    elapsed = time.perf_counter() - start
    print(f"Model loaded in {elapsed:.1f}s (prefix caching enabled, batch_wait={BATCH_WAIT_MS}ms, max_batch={MAX_BATCH_SIZE}, request_timeout={REQUEST_TIMEOUT_S}s)")

    # Start batch worker
    batch_queue = asyncio.Queue()
    batch_worker_task = asyncio.create_task(batch_worker())

    yield

    batch_worker_task.cancel()
    try:
        await batch_worker_task
    except asyncio.CancelledError:
        pass  # Expected: we just cancelled it above
    print("Shutting down...")

app = FastAPI(title="OpenVINO GenAI Server", lifespan=lifespan)

def count_tokens(text: str) -> int:
    """Count tokens in text."""
    encoded = tokenizer.encode(text)
    ids = encoded.input_ids
    if hasattr(ids, 'shape'):
        return ids.shape[-1]
    return len(ids)

@app.get("/health")
async def health():
    queue_size = batch_queue.qsize() if batch_queue else 0
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "queue_depth": queue_size,
        "request_timeout_s": REQUEST_TIMEOUT_S,
    }

@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": MODEL_NAME,
            "object": "model",
            "owned_by": "local",
        }]
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Validate n parameter — only single generation supported
    if request.n is not None and request.n != 1:
        raise HTTPException(status_code=400, detail="Only n=1 is supported")

    # Validate model field if provided
    if request.model and request.model != MODEL_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{request.model}' not found. Available: {MODEL_NAME}"
        )

    # Build messages for chat template
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    # Apply chat template with thinking disabled via template variable.
    # This pre-fills an empty <think></think> block so the model skips
    # internal reasoning and outputs content directly.
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True,
        extra_context={'enable_thinking': False}
    )

    # Configure generation
    gen_config = ov_genai.GenerationConfig()
    gen_config.max_new_tokens = request.max_tokens or 2048
    gen_config.temperature = request.temperature if request.temperature and request.temperature > 0 else 0.01
    gen_config.do_sample = (request.temperature or 0) > 0
    gen_config.top_p = request.top_p or 1.0
    # Derive EOS token from tokenizer; include any extra stop token IDs from CLI
    eos_id = tokenizer.get_eos_token_id()
    gen_config.eos_token_id = eos_id
    gen_config.stop_token_ids = {eos_id} | EXTRA_STOP_TOKEN_IDS

    # Submit to batch queue and wait for result
    loop = asyncio.get_running_loop()
    batch_req = BatchRequest(prompt=prompt, gen_config=gen_config, future=loop.create_future())
    await batch_queue.put(batch_req)
    output_text, elapsed = await batch_req.future

    # Count output tokens to detect truncation
    raw_output_tokens = count_tokens(output_text)

    # Strip any thinking blocks from output (safety net — should be empty with
    # enable_thinking=False, but handles edge cases like leading whitespace or
    # multiple blocks)
    output_text = re.sub(r"<think>.*?</think>\s*", "", output_text, flags=re.DOTALL)
    # Handle unclosed <think> tag (model started thinking but didn't close)
    if "<think>" in output_text:
        output_text = output_text[:output_text.index("<think>")] + output_text[output_text.index("<think>") + len("<think>"):]
    output_text = output_text.lstrip("\n")

    # Detect truncation: if raw tokens used nearly all of max_new_tokens budget
    finish_reason = "stop"
    if raw_output_tokens >= gen_config.max_new_tokens - 2:
        finish_reason = "length"

    # Count tokens
    prompt_tokens = count_tokens(prompt)
    completion_tokens = count_tokens(output_text)

    # Build response
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    response = ChatCompletionResponse(
        id=resp_id,
        created=int(time.time()),
        model=MODEL_NAME,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=output_text),
                finish_reason=finish_reason
            )
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )
    )

    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenVINO GenAI Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Bind address (use 0.0.0.0 to expose on network)")
    parser.add_argument("--model-dir", type=str, default=MODEL_DIR or None,
                        required=not MODEL_DIR,
                        help="Path to OpenVINO IR model directory (or set OV_MODEL_DIR env)")
    parser.add_argument("--batch-wait-ms", type=int, default=BATCH_WAIT_MS)
    parser.add_argument("--max-batch-size", type=int, default=MAX_BATCH_SIZE)
    parser.add_argument("--cache-size-gb", type=int, default=CACHE_SIZE_GB,
                        help="KV cache size in GB (default: 8)")
    parser.add_argument("--request-timeout", type=int, default=REQUEST_TIMEOUT_S,
                        help="Per-request timeout in seconds; 0 = no timeout (default: 600)")
    parser.add_argument("--stop-token-ids", type=str, default="",
                        help="Comma-separated extra stop token IDs (beyond EOS)")
    args = parser.parse_args()

    MODEL_DIR = args.model_dir
    CACHE_DIR = MODEL_DIR + "/ov_cache"
    MODEL_NAME = os.path.basename(MODEL_DIR.rstrip("/"))
    BATCH_WAIT_MS = args.batch_wait_ms
    MAX_BATCH_SIZE = args.max_batch_size
    CACHE_SIZE_GB = args.cache_size_gb
    REQUEST_TIMEOUT_S = args.request_timeout
    if args.stop_token_ids:
        EXTRA_STOP_TOKEN_IDS = {int(x.strip()) for x in args.stop_token_ids.split(",")}

    uvicorn.run(app, host=args.host, port=args.port)
