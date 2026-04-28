# Usage Guide

## Setup

### Prerequisites

- Python 3.10+
- No external Python packages required for core tools
- **Optional** — for LLM-based semantic extraction:
  - `pip install -r requirements-llm.txt`
  - An OpenAI-compatible LLM endpoint (OpenAI API, Ollama, etc.)
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
| `gemini-2.5-flash` (Google) | Unknown | N/A | Cloud | Very good — fast, low cost (~$0.30/run), 1M token context, strong JSON mode |
| `gpt-4o` (OpenAI) | Unknown | N/A | Cloud | Best — accurate structured JSON, strong coreference, minimal hallucination |

### VRAM Quick Reference

| GPU VRAM | Maximum Model Size (Q4 quantization) |
|---|---|
| 8 GB | Up to 7B |
| 12 GB | Up to 14B |
| 16 GB | Up to 22B |
| 24 GB | Up to 32B |

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

Configure Ollama or any OpenAI-compatible server in `config/llm.json`:

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
  "batch_delay_ms": 200
}
```

> **Note:** Ollama exposes an OpenAI-compatible `/v1` endpoint, so the tooling connects to it through the OpenAI-compatible client path. Set `"provider": "ollama"` when targeting Ollama to enable Ollama-specific request options (`extra_body.options`). The default Ollama port (`:11434`) is also auto-detected regardless of the `provider` value.

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

| Field | Description |
|---|---|
| `max_tokens` | Default max output tokens for all LLM extraction calls. |
| `pc_max_tokens` | Max output tokens for **PC entity extraction** only. Defaults to `max_tokens` if omitted. The player-character entity accumulates context over many turns and may need a higher token limit to avoid truncation. |
| `entity_refresh_interval` | Every N turns, find and re-extract stale entities whose `last_updated_turn` has fallen behind by more than N turns. Default: `50`. Set to `0` to disable. |
| `entity_refresh_batch_size` | Base number of stale entities to refresh per interval. Default: `10`. For catalogs with 60+ entities the effective batch scales to `max(batch_size, catalog_size // 5)`, capped at 25. Refresh slots are allocated proportionally by type (characters 50%, locations 20%, items 20%, factions 10%) with overflow redistribution. Entities are prioritized by staleness (most stale first) with event-frequency tiebreaking. |
| `checkpoint_interval` | Save extraction progress to disk every N turns. Default: `25`. Lower values reduce data loss on OOM interruptions at the cost of more frequent disk writes. |
| `context_length` | Context window size in tokens. Passed to Ollama via `extra_body.options.num_ctx` (#175). The Modelfile variant is the primary mechanism for setting context size; this field provides a runtime override. Also used to derive the default entity context budget (25% of this value). |
| `entity_context_budget` | Optional. Explicit token budget for the known-entity section of the entity discovery prompt. When omitted, defaults to 25% of `context_length`. Recently-active entities (within the last 10 turns) get full detail; older entities are reduced to ID/name/type; entities exceeding the budget are omitted with a truncation note. Set this to a higher value if coreference quality degrades, or lower if discovery prompts are timing out. |
| `timeout_seconds` | HTTP timeout per LLM call in seconds. PC extraction uses the greater of `2×` this value and `120` seconds. |
| `retry_attempts` | Number of retries on LLM call failure. |
| `batch_delay_ms` | Delay between consecutive LLM calls in milliseconds. Prevents GPU thrashing. For cloud providers, a minimum of 2000ms is enforced automatically to avoid hitting per-minute rate limits. |
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

Use a local-only import folder (gitignored) for raw source text:

```bash
mkdir -p sessions/_import
# Place your transcript at:
# sessions/_import/session-001-full-transcript.txt
```

```bash
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
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

2. Configure `config/llm.json` (defaults to local Ollama):
   ```json
   {
     "provider": "openai",
     "base_url": "http://localhost:11434/v1",
     "model": "qwen2.5:14b-8k",
     "api_key_env": ""
   }
   ```

   For Ollama, also create a context-tuned model variant first — see
   [Setting the Context Size](#setting-the-context-size-ollama) above.

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
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
```

The pipeline processes each turn through four agents:
1. **Entity Discovery** — identify entities mentioned in the turn
2. **Entity Detail Extractor** — extract/update attributes per entity
3. **Relationship Mapper** — identify cross-entity relationships
4. **Event Extractor** — identify narrative events

Progress is checkpointed every `checkpoint_interval` turns (default 25, configurable in `config/llm.json`) and can resume after interruption.

### Detached Batch Execution (Recommended)

For long extraction runs, launch extraction in a detached process so work in
other VS Code chat sessions does not affect the running job.

Use the helper script from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_extraction_detached.ps1 `
  -Session sessions/session-import `
  -TranscriptFile sessions/_import/session-import-full-transcript.txt `
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
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt \
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
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
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
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt \
  --max-turns 25

# Review wiki pages in framework/catalogs/*/README.md
# If satisfied, continue:

# Batch 2: turns 26-50
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt \
  --start-turn 26 --max-turns 50

# Review again, then:

# Batch 3: turns 51-75
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt \
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
- `discovery_filtered` — array of entities rejected during filtering, each with `name`, `id`, and `reason` (`below_confidence_threshold` or `concept_prefix`)
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
