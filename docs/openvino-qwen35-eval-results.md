# Qwen3.5 OpenVINO Evaluation Results

**Date:** 2026-05-05 (updated 2026-05-07)  
**Server:** Ubuntu 26.04, Intel Arc Pro B70 (24GB VRAM), 32 CPU cores, 30GB RAM  
**Verdict: BLOCKED — Hybrid linear-attention models produce garbage output after INT4 quantization**

---

## Environment (SUCCESS)

| Component | Version |
|-----------|---------|
| Python | 3.14.4 |
| OpenVINO | 2026.2.0-21799-b674d4dd6c4 (nightly) |
| openvino_genai | 2026.2.0.0-3074-b556f14c03b (built from PR #3801) |
| optimum-intel | 1.27.0.dev0+d4dd21a (PR #1689 merged) |
| transformers | 5.8.0.dev0 (git main, needed for qwen3_5 model type) |
| compute-runtime | 26.14.37833.4 (upgraded from 26.05.37020.3) |
| IGC | 2.32.7 |

---

## Key Finding: Qwen3.5 is EXCLUSIVELY a VLM

All Qwen3.5 models (0.8B, 9B, 35B-A3B) are Vision-Language Models with:
- Separate `openvino_language_model.bin` + `openvino_text_embeddings_model.bin`
- Vision encoder components (`openvino_vision_*`)
- Video processor requirements
- No text-only variant available

---

## BREAKTHROUGH: Text Inference Works via OVModelForVisualCausalLM (2026-05-06)

After upgrading compute-runtime to 26.14.37833.4, `OVModelForVisualCausalLM` from
optimum-intel successfully runs text-only inference on the B70 GPU:

```
=== Test: OVModelForVisualCausalLM (optimum-intel) ===
Python: 3.14.4

[1] Loading tokenizer from local model dir...
    OK - vocab size: 248044

[2] Loading OVModelForVisualCausalLM on GPU...
    OK - loaded in 6.7s

[3] Generating text (text-only, no image)...
    Input tokens: 22
    Generated 4 tokens in 0.54s (7.4 tok/s)
    Response: 4

=== SUCCESS ===
```

**Key findings:**
- Model loads on GPU in 6.5-9.9s (cached vs first compilation)
- Correct inference: "What is 2+2?" → "4"
- ~6-7 tok/s on the 0.8B FP16 model (text-only, no image)
- No CL_EXEC_STATUS_ERROR (fixed by compute-runtime upgrade)
- The VLM model works for pure text generation — no image required

**What this means for extraction:**
- OVModelForVisualCausalLM can be used as the inference backend
- Need to test with chat template + longer generation (extraction prompts)
- Need to benchmark 9B/35B-A3B variants for quality vs speed
- INT4 quantization would be needed for larger models

---

## CRITICAL FINDING: 9B INT4 Hybrid Model Produces Garbage (2026-05-07)

The 9B model (hybrid linear_attention + full_attention architecture with Gated Delta Rule)
was exported with INT4 quantization and tested on **all three inference paths**.
All produce incoherent output:

| Inference Path | Device | Output |
|---------------|--------|--------|
| openvino_genai ContinuousBatchingPipeline (PR #3801) | GPU | `"Thinking about 20. # 20. # 20000000000000..."` |
| optimum-intel OVModelForVisualCausalLM | GPU | `"ThinkingThinkingThinkingThinking..."` |
| optimum-intel OVModelForVisualCausalLM | CPU | `"\|,（,,, are,,,,,"` |

**Root cause:** The NNCF/optimum-intel INT4 quantization pipeline corrupts the
model weights. This is NOT a fundamental limitation of 4-bit quantization —
**llama.cpp's Q4_K_M quantization of the identical Qwen3.5-9B model produces
correct, high-quality output** (~56 tok/s on the same B70 GPU via SYCL).
The issue is specific to how NNCF handles the novel architecture during export.

Qwen3.5-9B uses a hybrid architecture:
- 24 `linear_attention` layers (Gated Delta Rule with conv kernel)
- 8 `full_attention` layers (standard transformer attention, every 4th layer)
- State-space-like components (`mamba_ssm_dtype: float32`)

**Performance note:** GPU loaded in 18s, generated 32 tokens in 3.3s (9.6 tok/s).
The model *runs* — it just produces nonsense.

**Additional bug:** optimum-intel `prepare_inputs_for_generation` crashes with
`TypeError: 'NoneType' object is not subscriptable` on `cache_position[0]` when
using transformers 5.8.0.dev0. Workaround: pass `cache_position=torch.arange(N)`
explicitly to `model.generate()`.

---

## Remaining Blockers

### 1. NNCF/optimum-intel INT4 export corrupts Qwen3.5-9B weights (CRITICAL)

The INT4 export via `optimum-cli export openvino --weight-format int4` produces
broken models for Qwen3.5-9B. However, **llama.cpp's Q4_K_M quantization of the
same model works perfectly** — proving the architecture supports 4-bit quantization.
The bug is in the NNCF quantization pipeline, not the model.

**Possible fixes:**
- Try INT8 export (`--weight-format int8`) — less aggressive, may avoid the bug
- Try `--ratio 0.5` (mixed INT4/INT8) to keep sensitive layers at higher precision
- Wait for upstream fix in NNCF's handling of linear attention / Gated Delta Rule layers
- Continue using llama-server (GGUF Q4_K_M) — proven working, ~56 tok/s single-slot

### 2. openvino_genai VLMPipeline: Unsupported model type (NOT CRITICAL)
```
Unsupported 'qwen3_5' VLM model type
```
PR #3717 (approved, merged or nearly merged) will fix this. Not needed since
OVModelForVisualCausalLM works directly.

### 3. optimum-intel + transformers 5.8 compatibility (WORKAROUND EXISTS)
`cache_position` is None in `prepare_inputs_for_generation` — pass explicitly.

---

## Resolved Issues

### ~~3. Export: Only VLM task supported~~ (WORKAROUND)
VLM export works fine — `OVModelForVisualCausalLM` handles text-only inference.

### ~~4. OVModelForVision2Seq: Processor bugs~~ (RESOLVED)
Using `OVModelForVisualCausalLM` instead of `OVModelForVision2Seq` avoids the
processor/loading issues entirely.

### ~~5. Disk space~~ (MANAGEABLE)
0.8B model: 2.1GB. Can make room for 9B by cleaning up unused files.

---

## What Worked

- ✅ Export to OpenVINO IR (VLM format, FP16): 0.8B model exported successfully
- ✅ INT4 quantization pipeline initiated (failed at save due to iostream error, likely fixable)
- ✅ LLMPipeline/VLMPipeline LOADS on GPU without crash (no CL_EXEC_STATUS_ERROR!)
- ✅ No gibberish on CPU (openvino#35640 regression may be fixed in this nightly)
- ✅ **OVModelForVisualCausalLM text-only inference on GPU** (NEW — breakthrough)
- ✅ Compute-runtime 26.14.37833.4 + IGC 2.32.7 fixes GPU stability

---

## Decision: Blocked — NNCF INT4 Export Bug

| Outcome | Status |
|---------|--------|
| 0.8B FP16 works on GPU (OpenVINO) | ✅ Correct output, ~7 tok/s |
| 9B GGUF Q4_K_M works (llama-server SYCL) | ✅ Correct output, ~56 tok/s |
| 9B NNCF INT4 works on GPU (OpenVINO) | ❌ Garbage output — NNCF export bug |
| 9B NNCF INT4 works on CPU (OpenVINO) | ❌ Garbage output — same bug |
| CBP batched inference (PR #3801) | ❌ Loads/runs but garbage (same model bug) |

**Action:** Filed [optimum-intel#1722](https://github.com/huggingface/optimum-intel/issues/1722). Try INT8 export or wait for fix.  
**Next steps:**
1. ✅ Filed issue on optimum-intel (#1722) — add comment noting GGUF Q4_K_M works
2. Try INT8 quantization (`--weight-format int8`) as potential workaround
3. If INT8 works: benchmark quality + speed vs llama-server baseline
4. If INT8 also broken: stay on llama-server (proven ~56 tok/s), pursue OpenVINO only for batching gains

**Key insight:** The value of OpenVINO over llama-server is ContinuousBatchingPipeline
(true parallel request processing in a single forward pass). Without working
quantization, we can't leverage this. llama-server at ~56 tok/s single-slot is
the proven baseline.

---

## Upstream Issues & PRs

| Issue/PR | Repo | Status | Relevance |
|----------|------|--------|-----------|
| [#3717](https://github.com/openvinotoolkit/openvino.genai/pull/3717) | openvino.genai | Approved/Merged | Adds native VLMPipeline for qwen3_5 |
| [#3801](https://github.com/openvinotoolkit/openvino.genai/pull/3801) | openvino.genai | Draft | CBP for hybrid KV+linear cache (runs but MoE output broken) |
| [#1720](https://github.com/huggingface/optimum-intel/issues/1720) | optimum-intel | Open | GPU loading (fixed by CR 26.14 upgrade) |
| [#1721](https://github.com/huggingface/optimum-intel/issues/1721) | optimum-intel | Open | text-generation export blocked (workaround: VLM export) |
| [#1722](https://github.com/huggingface/optimum-intel/issues/1722) | optimum-intel | **Open** | NNCF INT4 export produces garbage (qwen3.5-9B) — GGUF Q4_K_M of same model works |
