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
    """Collect requests into batches and process them together."""
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

        # Process batch
        prompts = [r.prompt for r in batch]
        gen_configs = [r.gen_config for r in batch]

        start = time.perf_counter()
        try:
            results = await loop.run_in_executor(
                None, pipeline.generate, prompts, gen_configs
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
        except Exception as e:
            for req in batch:
                if not req.future.done():
                    req.future.set_exception(e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, tokenizer, batch_queue, batch_worker_task
    print(f"Loading model from {MODEL_DIR}...")
    start = time.perf_counter()

    sched_cfg = ov_genai.SchedulerConfig()
    sched_cfg.cache_size = 8  # 8GB KV cache
    sched_cfg.enable_prefix_caching = True

    pipeline = ov_genai.ContinuousBatchingPipeline(
        MODEL_DIR, sched_cfg, 'GPU', {'CACHE_DIR': CACHE_DIR}
    )
    tokenizer = pipeline.get_tokenizer()

    elapsed = time.perf_counter() - start
    print(f"Model loaded in {elapsed:.1f}s (prefix caching enabled, batch_wait={BATCH_WAIT_MS}ms, max_batch={MAX_BATCH_SIZE})")

    # Start batch worker
    batch_queue = asyncio.Queue()
    batch_worker_task = asyncio.create_task(batch_worker())

    yield

    batch_worker_task.cancel()
    try:
        await batch_worker_task
    except asyncio.CancelledError:
        pass
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
    return {"status": "ok", "model": MODEL_NAME}

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
    # Derive EOS token from tokenizer; fall back to model's default if available
    eos_id = tokenizer.get_eos_token_id()
    gen_config.eos_token_id = eos_id
    gen_config.stop_token_ids = {eos_id}

    # Submit to batch queue and wait for result
    loop = asyncio.get_running_loop()
    batch_req = BatchRequest(prompt=prompt, gen_config=gen_config, future=loop.create_future())
    await batch_queue.put(batch_req)
    output_text, elapsed = await batch_req.future

    # Count output tokens to detect truncation
    raw_output_tokens = count_tokens(output_text)

    # Strip thinking block if present (safety net — should be empty with enable_thinking=False)
    if output_text.startswith("<think>"):
        think_end = output_text.find("</think>")
        if think_end != -1:
            output_text = output_text[think_end + len("</think>"):].lstrip("\n")
        else:
            # No closing </think> — model emitted tag but went straight to content
            output_text = output_text[len("<think>"):].lstrip("\n")

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
        model=request.model or MODEL_NAME,
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
    args = parser.parse_args()

    MODEL_DIR = args.model_dir
    CACHE_DIR = MODEL_DIR + "/ov_cache"
    MODEL_NAME = os.path.basename(MODEL_DIR.rstrip("/"))
    BATCH_WAIT_MS = args.batch_wait_ms
    MAX_BATCH_SIZE = args.max_batch_size

    uvicorn.run(app, host=args.host, port=args.port)
