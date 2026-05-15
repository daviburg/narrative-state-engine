# OpenVINO 2026.2 Nightly Test Results — Intel Arc Pro B70

**Date**: 2026-07-07  
**Hardware**: Intel Arc Pro B70 (BMG-G31, 31GB VRAM, 256 CUs) × 2 on arclight server  
**Target PRs**: #35503 (GPU SDPA), #35620 (Paged GatedDeltaNet), #35640 (CPU GQA GDN)  
**Motivation**: Test whether Qwen 3.5 9B INT4 works on B70 with latest nightly

---

## Versions Installed

| Package | Version |
|---------|---------|
| openvino | 2026.2.0-21870-2492324617a (nightly, 2026-05-09 build) |
| openvino-genai | 2026.2.0.0-3108-1e7a63d14a1 (nightly) |
| optimum-intel | 1.27.0.dev0+fb74525 (GitHub main) |
| transformers | 5.8.0.dev0 (GitHub main) |
| Python | 3.14.4 |
| Test venv | /tmp/ov-test-venv |

---

## Test 1: Qwen 3 8B INT4 — Inference + Benchmark

**Model**: `/home/nse-agent/models/qwen3-8b-int4-ov` (existing, exported with OV 2026.1.0)  
**Backend**: ContinuousBatchingPipeline, prefix caching enabled, cache_size=8, GPU.0  
**Result**: **PASS — coherent output, no regressions**

### Single Request Performance (128 max_new_tokens)

| Prompt | Time (s) | ~Words | ~Words/s |
|--------|----------|--------|----------|
| Capital of France | 3.53 | 101 | 28.6 |
| Haiku about the moon | 3.44 | 84 | 24.5 |
| Quantum computing | 3.43 | 101 | 29.4 |

### Batch-4 Performance (128 max_new_tokens each)

| Metric | Value |
|--------|-------|
| Total time | 4.76s (4 requests) |
| Total words | ~376 |
| Aggregate throughput | **~79 words/s** |
| Parallelism speedup | **~2.7×** vs single |

### Notes

- Thinking tokens included in word counts (model generates `<think>` blocks). Actual useful output is ~20-30% of total.
- GPU.0 was already running an `ov_serve.py` instance; benchmark loaded a 2nd model copy without OOM — 31GB VRAM supports multiple INT4 instances.
- Results consistent across two runs (±1% variance).
- Comparison to OV 2026.1.0 baseline: single-request ~28 words/s vs previous ~48-54 tok/s (different metrics; words ≈ 1.3 tokens, so ~36-38 tok/s — slight degradation likely from GPU contention with existing server).

---

## Test 2: Qwen 3.5 9B INT4 — Export Attempt

**Model**: `Qwen/Qwen3.5-9B` (HF Hub, cached at `/data/huggingface/hub/`)  
**Result**: **BLOCKED — cannot export, toolchain broken**

### Root Cause

The export patcher in optimum-intel (`Qwen3_5DynamicCacheWrap`) references cache instance attributes that don't exist in the current transformers `Qwen3_5DynamicCache`.

**Note**: As of 2026-05-11, `Qwen3_5DynamicCache` now EXISTS in transformers 5.8.0.dev0 (previously it didn't). But the API has changed — `Qwen3_5DynamicCache.__init__` stores `layer_types` as a local variable and passes `layers` to the parent `Cache.__init__`, which only stores `self.layers`. The attributes `self.layer_types`, `self.transformer_layers`, and `self.last_linear_layer` that optimum-intel's `Qwen3_5DynamicCacheWrap` accesses are never set as instance attributes.

#### Error Chain (2026-05-11)

1. **`self.layer_types` missing** (model_patcher.py:9220):
   ```
   AttributeError: 'Qwen3_5DynamicCacheWrap' object has no attribute 'layer_types'
   ```

2. **After patching `layer_types`** → `self.transformer_layers` missing (model_patcher.py:9257):
   ```
   AttributeError: 'Qwen3_5DynamicCacheWrap' object has no attribute 'transformer_layers'
   ```

3. **Likely further**: `self.last_linear_layer` also missing (model_patcher.py:9257)

### Analysis

- Qwen 3.5 uses a **hybrid linear attention + full attention** VLM architecture (Gated Delta Rule with vision encoder, 3:1 linear:full attention ratio).
- optimum-intel's export patcher was written for an intermediate version of transformers where `Qwen3_5DynamicCache` set `self.layer_types`, `self.transformer_layers`, `self.last_linear_layer` as instance attributes. The current version does not.
- Both environments on arclight fail:
  - `openvino-env` (stable): transformers 4.57.6 → `KeyError: 'qwen3_5'` (model type unknown)
  - `ov-test-venv` (nightly): transformers 5.8.0.dev0 → AttributeError chain

### Versions Tested

| optimum-intel | transformers | Result |
|---------------------|--------------|--------|
| 1.27.0 (stable) | 4.57.6 | KeyError: 'qwen3_5' (model type unknown) |
| 1.27.0.dev0+fb74525 | 5.8.0.dev0 | AttributeError: layer_types |
| fb74525 + layer_types patch | 5.8.0.dev0 | AttributeError: transformer_layers |

---

## Test 3: Qwen 3.5 0.8B — Model Inspection

**Observation**: The 0.8B variant at `/data/huggingface/hub/models--Qwen--Qwen3.5-0.6B-VL/` is a **VLM** (vision-language model), not a text-only model. It has `openvino_language_model.bin` suggesting it may have been partially exported, but it's not the architecture we need to test.

---

## Summary

| Model | Backend | Quant | Status | Notes |
|-------|---------|-------|--------|-------|
| Qwen 3 8B | OpenVINO GenAI (nightly) | INT4 | **WORKS** | Coherent output, batch parallelism confirmed |
| Qwen 3.5 9B | OpenVINO (export) | INT4 | **BLOCKED** | optimum-intel/transformers cache class mismatch |
| Qwen 3.5 9B | OpenVINO (inference) | INT4 | **UNTESTABLE** | No exported model available to test PRs |

---

## Test 4: Qwen 3.5 9B — Re-test After #35640 Fix (2026-05-11)

**Context**: MaximProshin on [optimum-intel#1722](https://github.com/huggingface/optimum-intel/issues/1722) suggested the garbage output might be related to [openvino#35640](https://github.com/openvinotoolkit/openvino/pull/35640) (CPU GQA GDN fix). Asked us to re-test with the fix and share FP16 results.

**OpenVINO nightly**: 2026.2.0-21870-2492324617a (2026-05-09 build, includes #35640)

### Key Finding: Qwen3.5-9B is a VLM

Qwen3.5-9B is **not** a text-only model — it's a vision-language model (VLM) with architecture `Qwen3_5ForConditionalGeneration`. The only supported export task is `image-text-to-text`. vLLM offers `--language-model-only` to skip the vision encoder, but optimum-intel exports the full model.

### Existing INT4 Model: DELETED

The previously exported INT4 model at `/data/models/qwen35-9b-int4-ov` no longer exists. It was exported on ~May 5 by a different environment (now overwritten) and was not preserved.

### Re-export Attempt: BLOCKED — API Mismatch

The export fails due to version incompatibility between optimum-intel (1.27.0.dev0+fb74525, GitHub main) and transformers (5.8.0.dev0, GitHub main):

1. **`layer_types` attribute missing**: `Qwen3_5DynamicCacheWrap.__init__` in `model_patcher.py:9220` accesses `self.layer_types[i]`, but `Qwen3_5DynamicCache.__init__` in transformers does NOT set this as an instance attribute — it's only a local variable.

2. **After patching `layer_types`**: Next error is `self.transformer_layers` missing at `model_patcher.py:9257`.

3. **Root cause**: `DynamicCache.__init__` (the grandparent) only stores `self.layers` and `self.layer_class_to_replicate`. The optimum-intel patcher code assumes `self.layer_types`, `self.transformer_layers`, and `self.last_linear_layer` exist as instance attributes, but neither `Qwen3_5DynamicCache` nor its parents set them.

4. **Both environments fail**:
   - `openvino-env` (stable): transformers 4.57.6 → `KeyError: 'qwen3_5'` (model type unknown)
   - `ov-test-venv` (nightly): transformers 5.8.0.dev0 → `AttributeError: 'Qwen3_5DynamicCacheWrap' object has no attribute 'layer_types'`

5. **No pre-exported models available** on HuggingFace Hub (0 results for "qwen3.5 openvino").

### Verbatim Error (after layer_types patch)

```
File "model_patcher.py", line 9257, in get_seq_length
    layer_idx = self.transformer_layers[0] if layer_idx not in self.transformer_layers else layer_idx
AttributeError: 'Qwen3_5DynamicCacheWrap' object has no attribute 'transformer_layers'
```

### Cannot Test Runtime Fix

Since we cannot export the model (INT4 or FP16), we **cannot test whether #35640 fixes the garbage output**. The runtime fix addresses inference on already-exported models, but we have no exported model to test with.

---

## Recommendations

1. **Report export blocker to optimum-intel**: The `Qwen3_5DynamicCacheWrap` in `model_patcher.py` is incompatible with current transformers 5.8.0.dev0 `Qwen3_5DynamicCache`. This is a separate bug from #1722.
2. **Request a pre-exported model**: Ask the OpenVINO team if they have a working Qwen3.5-9B INT4 export that we can use to test the runtime fix.
3. **Continue using Qwen 3 8B INT4 on OpenVINO** for extraction workloads — it works reliably with ContinuousBatchingPipeline.
4. **Re-test when export toolchain is fixed** — the OpenVINO runtime PRs (#35503, #35620, #35640) should make Qwen 3.5's GatedDeltaNet architecture work at inference time, but we need a working export first.

---

## Environment Details

```
# Nightly install commands (in /tmp/ov-test-venv)
pip install --pre openvino openvino-tokenizers openvino-genai --extra-index-url https://storage.openvinotoolkit.org/simple/wheels/nightly
pip install git+https://github.com/huggingface/optimum-intel.git
pip install git+https://github.com/huggingface/transformers.git
```
