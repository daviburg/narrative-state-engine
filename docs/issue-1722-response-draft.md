@MaximProshin Thanks for the pointer to #35640. We installed the OpenVINO nightly (2026.2.0-21870-2492324617a, May 9 build) which includes that fix, but we're blocked from testing it.

**Problem: cannot re-export the model**

The previously exported Qwen3.5-9B INT4 model was deleted, and we cannot re-export with the current toolchain. The `Qwen3_5DynamicCacheWrap` in `model_patcher.py` references instance attributes that are not set by the current `Qwen3_5DynamicCache` in transformers 5.8.0.dev0:

1. `self.layer_types` — `Qwen3_5DynamicCache.__init__` creates this as a local variable, passes it to `super().__init__(layers=layers)`, but never stores it as `self.layer_types`
2. After patching `layer_types`: `self.transformer_layers` — not set anywhere in the Cache hierarchy
3. Likely also: `self.last_linear_layer`

```
AttributeError: 'Qwen3_5DynamicCacheWrap' object has no attribute 'layer_types'
```

**Environment:**
- openvino: 2026.2.0-21870-2492324617a (nightly)
- openvino-genai: 2026.2.0.0-3108-1e7a63d14a1 (nightly)
- optimum-intel: 1.27.0.dev0+fb74525 (GitHub main)
- transformers: 5.8.0.dev0 (GitHub main)
- Hardware: Intel Arc Pro B70 (BMG-G31), Ubuntu 26.04

**What we tried:**
- `optimum-cli export openvino -m Qwen/Qwen3.5-9B --weight-format int4 --task image-text-to-text --trust-remote-code` → `AttributeError: layer_types`
- Monkey-patched `self.layer_types = getattr(config, 'layer_types', [])` after `super().__init__()` → `AttributeError: transformer_layers`
- Older environment with transformers 4.57.6 → `KeyError: 'qwen3_5'` (model type not recognized)
- No pre-exported Qwen3.5 OpenVINO models found on HuggingFace Hub

**Question:** Could you share a working pre-exported Qwen3.5-9B model (INT4 or FP16) so we can test the runtime fix? Or could you point us to a compatible version combination of optimum-intel + transformers that can export this model?

We're happy to run the inference tests once we have a model — we have the nightly runtime with #35640 ready to go.
