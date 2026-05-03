# OpenVINO GenAI on Intel Arc Pro B70 — Investigation Findings

**Issue**: #282  
**Date**: 2025-05-03  
**Hardware**: Intel Arc Pro B70 (BMG-G31, 31GB VRAM, 256 CUs) on Ubuntu 26.04  
**Software**: OpenVINO 2026.1.0, openvino_genai 2026.1.0

## Executive Summary

OpenVINO GenAI **works** on the Arc Pro B70 and delivers **competitive** performance compared to llama-server's SYCL backend in initial testing. Warm-state generation averages ~48–54 tok/s vs the 52.7 tok/s llama-server baseline. However, this initial investigation has significant methodological gaps (see [Caveats](#caveats--investigation-gaps) below) that make the comparison unreliable.

**Status**: Phase 1 complete (proof of viability). Phase 2 required for a fair comparison.

**Recommendation**: Proceed with Phase 2 follow-up investigation to resolve the methodological issues before drawing conclusions. See [Follow-Up Tasks](#follow-up-tasks-phase-2).

---

## What Works

### GPU Detection & Inference
- OpenVINO 2026.1.0 (pip) correctly detects the B70 as `Intel(R) Graphics [0xe223] (dGPU)`
- `LLMPipeline` loads and runs INT4 models on GPU successfully
- Model compilation (first load) takes ~10-16s; subsequent loads with cache would be faster
- Generation produces coherent output

### Model Conversion
- `optimum-cli export openvino` successfully converts Qwen3-8B to INT4 (symmetric, group 128)
- Output model: 4.5GB (vs 5.5GB for Q4_K_M GGUF)
- Quantization breakdown: 85% INT4_SYM (weights), 15% INT8_ASYM (embeddings/head)
- Conversion time: ~5 minutes on CPU (Xeon Silver 4110)

### Benchmark Results

**Model**: Qwen3-8B INT4 (proxy for Qwen3.5-9B)  
**Baseline**: Qwen3.5-9B Q4_K_M via llama-server SYCL = 52.7 tok/s

| Test | Wall Time | Approx Tokens | tok/s | vs Baseline |
|------|-----------|--------------|-------|-------------|
| 200-token gen (warm) | 4.59s | ~247 | **53.9** | 102% |
| 512-token gen (warm) | 13.20s | ~564 | **42.7** | 81% |
| **Average (warm)** | — | — | **48.3** | **92%** |

**Notes:**
- These numbers include `<think>` tokens (Qwen3 thinking mode was active)
- The llama-server baseline uses `--reasoning off` which eliminates thinking overhead
- First-load compilation adds 10-16s one-time cost (cacheable with `ov::cache_dir`)
- The 200-token test is dominated by decode speed; 512-token test shows more realistic sustained throughput

---

## What Doesn't Work

### 1. GGUF Direct Loading
```
RuntimeError: gguf_tensor_to_f16 failed
```
The `openvino_genai` GGUF reader cannot load Q4_K_M quantized tensors from our Qwen3.5-9B GGUF. OVMS documentation claims GGUF support for Qwen2.5 and Qwen3, but:
- Qwen3.5 architecture (`qwen3_5` model type) is too new
- The GGUF tensor format conversion may not support all k-quant types

### 2. OVMS Bare-Metal Binary
```
error while loading shared libraries: libxml2.so.2: cannot open shared object file
```
Ubuntu 26.04 ships libxml2.so.**16** (major ABI break from .so.2). The OVMS v2026.1 binary is compiled for Ubuntu 24 and links against the old ABI. No Ubuntu 26 binary is available.

### 3. Qwen3.5 in Stable Transformers
```
ValueError: model type `qwen3_5` but Transformers does not recognize this architecture
```
Transformers 4.57.6 doesn't support Qwen3.5 yet. The dev branch (5.8.0) adds support but breaks `optimum-intel` due to API changes. Used Qwen3-8B as a proxy instead.

### 4. Docker
Not installed on arclight. OVMS Docker is the primary deployment path for GPU inference, and it's not available.

---

## Serving Options Analysis

For our extraction pipeline (OpenAI-compatible chat completions endpoint):

| Option | Status | Notes |
|--------|--------|-------|
| OVMS bare-metal | **Blocked** | ABI incompatible with Ubuntu 26 |
| OVMS Docker | **Blocked** | Docker not installed |
| vLLM + OpenVINO | **Not tested** | Requires Docker or complex build |
| openvino_genai + FastAPI | **Viable** | Needs custom wrapper (~100 lines) |
| llama-server SYCL (current) | **Working** | Already integrated, proven |

The FastAPI wrapper approach is feasible but adds maintenance burden for marginal performance gain.

---

## Performance Analysis

### Why Not a Clear Win?

> **CAVEAT**: The analysis below was written during initial investigation and contains
> assumptions now known to be questionable. See [Caveats](#caveats--investigation-gaps).

1. ~~**llama-server SYCL is already well-optimized**~~ — Subsequent investigation found the llama-server SYCL build was **not** well-optimized and missed significant performance opportunities discovered by researching other projects. This same bias likely affected the OpenVINO evaluation.

2. **INT4 dynamic quantization overhead** — OpenVINO's INT4 inference uses dynamic dequantization at runtime, which has compute cost. llama-server's Q4_K_M uses a different (ggml-specific) dequant path that may be more efficient for decode.

3. **XMX utilization** — The B70's XMX (matrix extension) units are designed for larger batch sizes. Single-request inference (batch=1) doesn't fully exploit XMX parallelism. OpenVINO's advantage would be more pronounced with batched requests. **NOTE**: Batched requests were not tested but should have been — other B70 users report batching works fine on this hardware.

4. **Thinking mode** — The benchmark ran with thinking mode active, inflating token counts and wasting generation budget. This was noted as a blocker but not actually resolved. **NOTE**: Disabling thinking in Qwen3 is achievable via chat template configuration (`/no_think` or system prompt instruction) — this should have been tested.

### Estimated Full-Run Time (344 turns)

At 48.3 tok/s average (warm), with thinking overhead:
- Estimated: ~12-14 hours (worse than the 10.73h llama-server baseline)
- At 53.9 tok/s (short generation, warm): ~9-10 hours (marginal improvement)

---

## Setup on Arclight

### What was installed:
```bash
# Python venv with OpenVINO
~/openvino-env/  (OpenVINO 2026.1.0, openvino_genai, optimum-intel, torch CPU)

# Converted model
~/models/qwen3-8b-int4-ov/  (4.5GB, Qwen3-8B INT4 symmetric)

# OVMS binary (non-functional due to ABI)
/tmp/ovms/ovms/bin/ovms

# Benchmark scripts
/tmp/bench_ov_v2.py
~/bench_results.txt
```

### To reproduce:
```bash
source ~/openvino-env/bin/activate
python3 /tmp/bench_ov_v2.py
cat ~/bench_results.txt
```

---

## Caveats & Investigation Gaps

This initial investigation has significant methodological problems that invalidate a definitive conclusion:

1. **Unfair comparison — thinking mode active**: The OpenVINO benchmark ran with Qwen3's thinking mode generating `<think>` tokens, while the llama-server baseline uses `--reasoning off`. This wastes 60-80% of generated tokens on internal monologue and makes the tok/s figures incomparable. Disabling thinking is achievable (chat template manipulation, system prompt instructions) and must be done for Phase 2.

2. **No batching tested**: The benchmark only tested single-request serial inference (`LLMPipeline`). OpenVINO's `ContinuousBatchingPipeline` and OVMS both support concurrent request batching, which is where XMX units excel. Other B70 users report batching works fine. The assumption that "our use case is batch=1" is questionable — the extraction pipeline could be parallelized.

3. **MoE models not tested**: OpenVINO 2025.4+ has explicit Mixture-of-Experts optimization for GPU (validated for Qwen3-30B-A3B). MoE was a major performance win on RTX 4070 via Ollama in past tests. This avenue was not explored at all.

4. **OVMS not tested**: The primary serving solution (OVMS Docker) was dismissed because Docker wasn't installed, rather than installing Docker. OVMS provides OpenAI-compatible API, native GGUF support, prefix caching, and continuous batching — all directly relevant to our workload.

5. **llama-server baseline was flawed**: A separate investigation found the llama-server SYCL setup was NOT well-optimized and missed significant performance improvements. The 52.7 tok/s "baseline" is likely an underperforming reference point. The same investigator bias (accepting first results without deeper research) likely affected this OpenVINO evaluation.

6. **No model cache used**: First-run kernel compilation dominates timing but is a one-time cost with `ov::cache_dir`. Benchmarks should use pre-compiled cache for steady-state measurement.

---

## Follow-Up Tasks (Phase 2)

These must be completed before drawing conclusions:

### P0 — Required for fair comparison
- [ ] **Install Docker on arclight** — Unblocks OVMS, the primary OpenVINO serving path
- [ ] **Deploy OVMS via Docker with GPU passthrough** — Test native GGUF loading and OpenAI API
- [ ] **Disable thinking mode** — Configure chat template or system prompt to suppress `<think>` generation for extraction workload
- [ ] **Benchmark with batched requests** — Use `ContinuousBatchingPipeline` or OVMS with 2-4 concurrent requests to test XMX utilization
- [ ] **Enable model compilation cache** — Use `ov::cache_dir` or OVMS `--cache_dir` for steady-state benchmarks

### P1 — High-value exploration
- [ ] **Test MoE model (Qwen3-30B-A3B)** — OpenVINO has explicit MoE GPU optimization; this model may outperform dense 8-9B models for extraction quality at acceptable speed
- [ ] **Test prefix caching** — Our extraction pipeline reuses the same system prompt across all turns; OVMS's optimized prefix caching could dramatically reduce TTFT
- [ ] **Research OpenVINO-specific optimizations** — Look at what other Arc users have achieved; check Intel's benchmark publications for B-series GPUs
- [ ] **Benchmark prompt evaluation (prefill) speed** — Our extraction prompts are 4K-16K tokens; prefill performance matters as much as decode

### P2 — If performance is competitive
- [ ] **Run extraction smoke test** (turns 1-10) through OVMS endpoint
- [ ] **Compare output quality** between OpenVINO INT4 and llama-server Q4_K_M
- [ ] **Measure end-to-end extraction time** including all pipeline overhead

---

## Key Takeaways

- OpenVINO on Arc Pro B70 is **functional** — GPU detection, model loading, and inference all work
- Initial benchmarks show **competitive raw decode speed** (~48-54 tok/s) even with methodological handicaps (thinking mode active, no batching, no caching)
- The investigation was **incomplete** — critical features (OVMS, batching, MoE, thinking suppression) were not tested due to accepting blockers at face value rather than resolving them
- **Phase 2 is required** before any recommendation can be made
- The 52.7 tok/s llama-server baseline is itself suboptimal (confirmed by separate investigation), making the comparison doubly unreliable
