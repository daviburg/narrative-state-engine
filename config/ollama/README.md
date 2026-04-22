# Ollama Model Variants

Pre-tuned Modelfiles for use with [Ollama](https://ollama.com/). Each variant
sets `num_ctx` to a fixed context length because Ollama's OpenAI-compatible
`/v1` endpoint ignores runtime `num_ctx` overrides — the context size must be
baked into the model definition.

## Quick start

```bash
# Pull the base model (once)
ollama pull qwen2.5:14b

# Create a variant that fits your GPU
ollama create qwen2.5:14b-8k -f config/ollama/qwen2.5-14b-8k.Modelfile

# Point config/llm.json at the new name
#   "model": "qwen2.5:14b-8k"
#   "context_length": 8192
```

## Choosing a context size

| Variant | Context | VRAM (approx) | Best for |
|---------|---------|---------------|----------|
| `qwen2.5-14b-4k` | 4 096 | ~9.1 GB | 8 GB GPUs (tight fit) |
| `qwen2.5-14b-8k` | 8 192 | ~9.8 GB | **12 GB GPUs (recommended)** |
| `qwen2.5-14b-16k` | 16 384 | ~11.2 GB | 16 GB+ GPUs |

Larger context lets the LLM see more of the prompt without truncation, which
improves extraction quality for longer DM turns and PC entity updates. But
context that exceeds GPU VRAM spills to CPU RAM and slows inference
dramatically.

**Rule of thumb:** pick the largest context that keeps total VRAM under your
GPU limit with ~1 GB headroom for the OS and display.

## After creating the variant

Update two fields in `config/llm.json`:

```jsonc
{
  "model": "qwen2.5:14b-8k",       // ← variant name you created
  "context_length": 8192            // ← must match the Modelfile
}
```

## Adding new variants

To add a variant for a different base model or context size, create a new
Modelfile following the naming pattern `{model}-{context}.Modelfile`:

```
FROM <base-model>
PARAMETER num_ctx <context-size>
```
