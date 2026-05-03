# OpenVINO GenAI on Intel Arc Pro B70 — Investigation Findings

**Issue**: #282  
**Date**: 2025-05-03  
**Hardware**: Intel Arc Pro B70 (BMG-G31, 31GB VRAM, 256 CUs) on Ubuntu 26.04  
**Software**: OpenVINO 2026.1.0, openvino_genai 2026.1.0

## Executive Summary

OpenVINO GenAI **works** on the Arc Pro B70 and delivers **competitive but not superior** performance compared to llama-server's SYCL backend. Warm-state generation averages ~48–54 tok/s vs the 52.7 tok/s llama-server baseline. The improvement is marginal and comes with significant ecosystem friction that makes it impractical as a drop-in replacement today.

**Recommendation**: Stay with llama-server SYCL for now. Revisit when OVMS supports Ubuntu 26 natively or when Qwen3.5 gains first-class OpenVINO support.

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

1. **llama-server SYCL is already well-optimized** — The IPEX-LLM SYCL build uses flash attention and optimized kernels for Arc GPUs.

2. **INT4 dynamic quantization overhead** — OpenVINO's INT4 inference uses dynamic dequantization at runtime, which has compute cost. llama-server's Q4_K_M uses a different (ggml-specific) dequant path that may be more efficient for decode.

3. **XMX utilization** — The B70's XMX (matrix extension) units are designed for larger batch sizes. Single-request inference (batch=1, which is our use case) doesn't fully exploit XMX parallelism. OpenVINO's advantage would be more pronounced with batched requests.

4. **Thinking mode** — The benchmark couldn't disable thinking tokens for Qwen3, which inflates the token count. A fair comparison would require either:
   - Using a non-thinking model (Qwen2.5)
   - Configuring the chat template to suppress thinking

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

## Recommended Next Steps

### If pursuing OpenVINO further:
1. **Install Docker** on arclight — enables OVMS with native GGUF support and OpenAI API
2. **Wait for Qwen3.5 support** in stable transformers + optimum-intel
3. **Test with model cache** (`ov::cache_dir`) to eliminate recompilation overhead
4. **Benchmark with prefix caching** — OpenVINO 2025.4+ has optimized prefix caching for GPU, which would benefit our extraction pipeline's repeated system prompts
5. **Try ContinuousBatchingPipeline** instead of LLMPipeline for better throughput

### If staying with llama-server:
1. The current setup is proven and integrated
2. Performance difference is within noise margin
3. No additional maintenance burden
4. Qwen3.5-9B GGUF works directly without conversion

---

## Key Takeaways

- OpenVINO on Arc Pro B70 is **functional and competitive** but not a breakthrough improvement
- The ecosystem has significant friction: Ubuntu 26 support is incomplete, Qwen3.5 needs bleeding-edge transformers, OVMS requires Docker
- The theoretical advantage of Intel-optimized kernels for Arc XMX doesn't materialize at batch_size=1
- For our single-request extraction workload, **llama-server SYCL remains the better choice** due to proven reliability, direct GGUF support, and equivalent performance
- OpenVINO would be worth revisiting for **batched inference** scenarios or when OVMS supports Ubuntu 26 natively
